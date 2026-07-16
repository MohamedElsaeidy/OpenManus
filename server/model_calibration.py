from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional


MIN_CONTEXT = 8_192
CONTEXT_GRANULARITY = 1_024
MAX_SEARCH_STEPS = 10


def _bytes_from_memory_value(value: float, unit: str) -> int:
    multipliers = {
        "b": 1,
        "kb": 1_000,
        "kib": 1_024,
        "mb": 1_000**2,
        "mib": 1_024**2,
        "gb": 1_000**3,
        "gib": 1_024**3,
    }
    return int(value * multipliers[unit.strip().lower()])


def parse_lms_estimate(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    patterns = {
        "estimated_gpu_bytes": r"Estimated GPU Memory:\s*([0-9.]+)\s*([KMGT]i?B)",
        "estimated_total_bytes": r"Estimated Total Memory:\s*([0-9.]+)\s*([KMGT]i?B)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            result[key] = _bytes_from_memory_value(
                float(match.group(1)), match.group(2)
            )
    confidence = re.search(r"Confidence:\s*([A-Za-z]+)", output)
    if confidence:
        result["confidence"] = confidence.group(1).lower()
    result["guardrails_allow"] = "may be loaded" in output.lower()
    return result


def parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
    devices = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            devices.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "total_bytes": int(float(parts[2]) * 1024**2),
                    "used_bytes": int(float(parts[3]) * 1024**2),
                    "free_bytes": int(float(parts[4]) * 1024**2),
                }
            )
        except (TypeError, ValueError):
            continue
    return devices


def read_resource_snapshot() -> dict[str, Any]:
    gpu_devices: list[dict[str, Any]] = []
    gpu_source = "unavailable"
    nvidia_smi = os.getenv("OPENMANUS_NVIDIA_SMI") or shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            completed = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=index,name,memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode == 0:
                gpu_devices = parse_nvidia_smi(completed.stdout)
                if gpu_devices:
                    gpu_source = "nvidia-smi"
        except Exception:
            pass

    ram_total = 0
    ram_available = 0
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        ram_total = values.get("MemTotal", 0)
        ram_available = values.get("MemAvailable", values.get("MemFree", 0))
    except Exception:
        pass

    gpu_total = sum(device["total_bytes"] for device in gpu_devices)
    gpu_used = sum(device["used_bytes"] for device in gpu_devices)
    return {
        "gpu": {
            "source": gpu_source,
            "devices": gpu_devices,
            "total_bytes": gpu_total,
            "used_bytes": gpu_used,
            "free_bytes": max(0, gpu_total - gpu_used),
            "used_percent": round(gpu_used * 100 / gpu_total, 2) if gpu_total else None,
        },
        "ram": {
            "source": "procfs" if ram_total else "unavailable",
            "total_bytes": ram_total,
            "available_bytes": ram_available,
            "used_bytes": max(0, ram_total - ram_available),
            "used_percent": round((ram_total - ram_available) * 100 / ram_total, 2)
            if ram_total
            else None,
        },
    }


def resource_fit(
    snapshot: dict[str, Any],
    *,
    gpu_target_percent: float,
    ram_target_percent: float,
) -> tuple[bool, str]:
    gpu_percent = (snapshot.get("gpu") or {}).get("used_percent")
    if gpu_percent is not None and gpu_percent > gpu_target_percent:
        return (
            False,
            f"GPU use {gpu_percent:.1f}% exceeds {gpu_target_percent:.1f}% target",
        )
    ram_percent = (snapshot.get("ram") or {}).get("used_percent")
    if ram_percent is not None and ram_percent > ram_target_percent:
        return (
            False,
            f"RAM use {ram_percent:.1f}% exceeds {ram_target_percent:.1f}% target",
        )
    return True, "within resource targets"


class LMStudioCalibrationRunner:
    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: Optional[str],
        embedding_model: Optional[str],
        gpu_target_percent: float,
        ram_target_percent: float,
        max_context: Optional[int],
        status_callback: Callable[..., None],
    ) -> None:
        parsed = urllib.parse.urlparse(base_url.strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Invalid LM Studio base URL")
        self.root = f"{parsed.scheme}://{parsed.netloc}"
        self.host = parsed.hostname or ""
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.model_id = model_id.strip()
        self.api_key = api_key
        self.embedding_model = (embedding_model or "").strip()
        self.gpu_target_percent = gpu_target_percent
        self.ram_target_percent = ram_target_percent
        self.requested_max_context = max_context
        self.status = status_callback
        self.lms_cli = self._find_lms_cli()
        self.model_size_bytes = 0
        self.probes: list[dict[str, Any]] = []

    @staticmethod
    def _find_lms_cli() -> Optional[str]:
        candidates = [
            os.getenv("OPENMANUS_LMS_CLI", ""),
            shutil.which("lms") or "",
            os.path.expanduser("~/.lmstudio/bin/lms"),
            "/opt/lmstudio/bin/lms",
        ]
        return next(
            (
                candidate
                for candidate in candidates
                if candidate
                and os.path.isfile(candidate)
                and os.access(candidate, os.X_OK)
            ),
            None,
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        timeout: int = 180,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.root}/api/v1{path}",
            method=method,
            headers=headers,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(detail or f"LM Studio returned HTTP {exc.code}") from exc

    def _models(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models", timeout=15)
        models = payload.get("models", payload.get("data", []))
        return [item for item in models if isinstance(item, dict)]

    def _matches_model(self, row: dict[str, Any], model_id: str) -> bool:
        values = {
            str(row.get("key") or ""),
            str(row.get("id") or ""),
            str(row.get("selected_variant") or ""),
        }
        values.update(str(item) for item in (row.get("variants") or []))
        return model_id in values or any(
            value
            and (value.startswith(f"{model_id}@") or model_id.startswith(f"{value}@"))
            for value in values
        )

    def _model_row(self, model_id: str) -> Optional[dict[str, Any]]:
        return next(
            (row for row in self._models() if self._matches_model(row, model_id)),
            None,
        )

    def _unload(self, model_id: str) -> None:
        row = self._model_row(model_id)
        if not row:
            return
        for instance in row.get("loaded_instances") or []:
            instance_id = str((instance or {}).get("id") or "")
            if not instance_id:
                continue
            try:
                self._request(
                    "POST", "/models/unload", {"instance_id": instance_id}, timeout=30
                )
            except Exception:
                pass

    def _load_embedding(self) -> None:
        if not self.embedding_model:
            return
        self._unload(self.embedding_model)
        self._request(
            "POST",
            "/models/load",
            {"model": self.embedding_model, "echo_load_config": True},
            timeout=90,
        )

    def estimate(self, context_length: int) -> dict[str, Any]:
        if not self.lms_cli:
            return {"available": False, "source": "unavailable"}
        command = [
            self.lms_cli,
            "load",
            "--estimate-only",
            self.model_id,
            "--context-length",
            str(context_length),
            "--gpu",
            "max",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            parsed = parse_lms_estimate(output)
            return {
                "available": completed.returncode == 0,
                "source": "lms estimate",
                **parsed,
            }
        except Exception as exc:
            return {"available": False, "source": "lms estimate", "error": str(exc)}

    def _load_with_cli(self, context_length: int) -> None:
        if not self.lms_cli:
            raise RuntimeError("LM Studio CLI is not available")
        command = [
            self.lms_cli,
            "load",
            self.model_id,
            "--gpu",
            "max",
            "--context-length",
            str(context_length),
            "--yes",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(detail or "LM Studio CLI load failed")

    def load_profile(
        self,
        *,
        context_length: int,
        kv_cache: str,
        request_full_gpu: bool,
    ) -> dict[str, Any]:
        self._unload(self.model_id)
        unloaded_snapshot = read_resource_snapshot()
        kv_on_gpu = kv_cache == "gpu"
        used_cli = bool(request_full_gpu and kv_on_gpu and self.lms_cli)
        if used_cli:
            self._load_with_cli(context_length)
        else:
            self._request(
                "POST",
                "/models/load",
                {
                    "model": self.model_id,
                    "context_length": context_length,
                    "flash_attention": True,
                    "offload_kv_cache_to_gpu": kv_on_gpu,
                    "echo_load_config": True,
                },
                timeout=180,
            )
        self._load_embedding()
        time.sleep(1)

        row = self._model_row(self.model_id)
        instances = (row or {}).get("loaded_instances") or []
        config = dict((instances[0] or {}).get("config") or {}) if instances else {}
        applied_context = int(config.get("context_length") or 0)
        applied_kv = config.get("offload_kv_cache_to_gpu")
        if applied_context != context_length:
            raise RuntimeError(
                f"LM Studio applied {applied_context:,} context instead of {context_length:,}"
            )
        if applied_kv is not None and bool(applied_kv) != kv_on_gpu:
            location = "GPU" if applied_kv else "RAM"
            raise RuntimeError(
                f"LM Studio placed KV cache in {location}, not {kv_cache.upper()}"
            )

        snapshot = read_resource_snapshot()
        gpu_before = (unloaded_snapshot.get("gpu") or {}).get("used_bytes")
        gpu_after = (snapshot.get("gpu") or {}).get("used_bytes")
        gpu_load_delta = (
            max(0, int(gpu_after) - int(gpu_before))
            if gpu_before is not None and gpu_after is not None
            else None
        )
        return {
            "applied_context": applied_context,
            "load_config": config,
            "resource_snapshot": snapshot,
            "gpu_load_delta_bytes": gpu_load_delta,
            "full_gpu_requested": request_full_gpu,
            "full_gpu_request_verified": used_cli,
            "kv_cache": kv_cache,
        }

    def _probe(
        self,
        *,
        mode: str,
        context_length: int,
        kv_cache: str,
        request_full_gpu: bool,
    ) -> dict[str, Any]:
        estimate = self.estimate(context_length)
        try:
            loaded = self.load_profile(
                context_length=context_length,
                kv_cache=kv_cache,
                request_full_gpu=request_full_gpu,
            )
            fits, reason = resource_fit(
                loaded["resource_snapshot"],
                gpu_target_percent=self.gpu_target_percent,
                ram_target_percent=self.ram_target_percent,
            )
            result = {
                "mode": mode,
                "context_length": context_length,
                "fits": fits,
                "reason": reason,
                "estimate": estimate,
                **loaded,
            }
        except Exception as exc:
            result = {
                "mode": mode,
                "context_length": context_length,
                "fits": False,
                "reason": str(exc),
                "estimate": estimate,
            }
        self.probes.append(result)
        return result

    def _search_profile(
        self,
        *,
        mode: str,
        max_context: int,
        kv_cache: str,
        request_full_gpu: bool,
        progress_start: int,
        progress_end: int,
    ) -> dict[str, Any]:
        low = min(MIN_CONTEXT, max_context)
        first = self._probe(
            mode=mode,
            context_length=low,
            kv_cache=kv_cache,
            request_full_gpu=request_full_gpu,
        )
        if not first["fits"]:
            raise RuntimeError(
                f"{mode.title()} mode failed at {low:,} tokens: {first['reason']}"
            )

        best = first
        lower = low
        upper = max_context
        for step in range(MAX_SEARCH_STEPS):
            if upper - lower < CONTEXT_GRANULARITY:
                break
            middle = (lower + upper + CONTEXT_GRANULARITY) // 2
            middle = (middle // CONTEXT_GRANULARITY) * CONTEXT_GRANULARITY
            if middle <= lower:
                break
            progress = progress_start + int(
                (progress_end - progress_start) * (step + 1) / MAX_SEARCH_STEPS
            )
            self.status(
                f"search_{mode}",
                f"Testing {mode} mode at {middle:,} tokens",
                progress,
                current_mode=mode,
                current_context=middle,
            )
            probe = self._probe(
                mode=mode,
                context_length=middle,
                kv_cache=kv_cache,
                request_full_gpu=request_full_gpu,
            )
            if probe["fits"]:
                best = probe
                lower = middle
            else:
                upper = middle - CONTEXT_GRANULARITY

        if lower < max_context and upper == max_context:
            final_probe = self._probe(
                mode=mode,
                context_length=max_context,
                kv_cache=kv_cache,
                request_full_gpu=request_full_gpu,
            )
            if final_probe["fits"]:
                best = final_probe
        return best

    def benchmark(self) -> dict[str, float]:
        def post(payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
            request = urllib.request.Request(
                f"{self.root}/v1/chat/completions",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    **(
                        {"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key
                        else {}
                    ),
                },
                data=json.dumps(payload).encode("utf-8"),
            )
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data, max(0.001, time.monotonic() - started)

        generation_speed = 0.0
        evaluation_speed = 0.0
        try:
            response, duration = post(
                {
                    "model": self.model_id,
                    "messages": [
                        {"role": "user", "content": "List ten prime numbers."}
                    ],
                    "temperature": 0,
                    "max_tokens": 96,
                }
            )
            generated = int((response.get("usage") or {}).get("completion_tokens") or 0)
            generation_speed = generated / duration if generated else 0.0
        except Exception:
            pass
        try:
            prompt = ("resource-aware local inference calibration " * 400).strip()
            response, duration = post(
                {
                    "model": self.model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 1,
                }
            )
            evaluated = int((response.get("usage") or {}).get("prompt_tokens") or 0)
            evaluation_speed = evaluated / duration if evaluated else 0.0
        except Exception:
            pass
        return {
            "generation_speed": round(generation_speed, 2),
            "evaluation_speed": round(evaluation_speed, 2),
        }

    def _profile_result(
        self,
        mode: str,
        probe: dict[str, Any],
        benchmark: dict[str, float],
    ) -> dict[str, Any]:
        snapshot = probe.get("resource_snapshot") or {}
        gpu_measured = (snapshot.get("gpu") or {}).get("source") == "nvidia-smi"
        full_request_verified = bool(probe.get("full_gpu_request_verified"))
        measured_gpu_delta = int(probe.get("gpu_load_delta_bytes") or 0)
        full_weight_delta = bool(
            gpu_measured
            and self.model_size_bytes
            and measured_gpu_delta >= self.model_size_bytes * 0.9
        )
        if full_request_verified and gpu_measured:
            residency = "observed_full_gpu_request"
            confidence = "high"
        elif full_request_verified:
            residency = "confirmed_full_gpu_request"
            confidence = "medium"
        elif full_weight_delta:
            residency = "full_weight_residency_inferred"
            confidence = "medium"
        else:
            residency = "lmstudio_auto_offload"
            confidence = "low"
        if mode == "deep" and full_request_verified:
            residency = "full_weight_residency_inferred"
            confidence = "medium" if gpu_measured else "low"
        return {
            "mode": mode,
            "context_length": int(probe["context_length"]),
            "kv_cache": probe.get("kv_cache", "gpu"),
            "flash_attention": bool(
                (probe.get("load_config") or {}).get("flash_attention", True)
            ),
            "generation_speed": benchmark["generation_speed"],
            "evaluation_speed": benchmark["evaluation_speed"],
            "residency": residency,
            "residency_confidence": confidence,
            "full_gpu_requested": bool(probe.get("full_gpu_requested")),
            "gpu_used_percent": (snapshot.get("gpu") or {}).get("used_percent"),
            "ram_used_percent": (snapshot.get("ram") or {}).get("used_percent"),
            "gpu_load_delta_bytes": measured_gpu_delta or None,
            "estimate": probe.get("estimate") or {},
            "load_config": probe.get("load_config") or {},
        }

    def run(self) -> dict[str, Any]:
        self.status("detect", "Reading LM Studio model and host resources", 5)
        models = self._models()
        llms = [row for row in models if row.get("type") in {"llm", "vlm"}]
        embeddings = [
            row for row in models if row.get("type") in {"embedding", "embeddings"}
        ]
        if not self.model_id and llms:
            self.model_id = str(llms[0].get("key") or llms[0].get("id") or "")
        if not self.embedding_model:
            loaded_embedding = next(
                (row for row in embeddings if row.get("loaded_instances")), None
            )
            if loaded_embedding:
                self.embedding_model = str(
                    loaded_embedding.get("key") or loaded_embedding.get("id") or ""
                )
        model = next(
            (row for row in llms if self._matches_model(row, self.model_id)), None
        )
        if not model:
            raise RuntimeError(f"Model '{self.model_id}' was not found in LM Studio")
        self.model_size_bytes = int(model.get("size_bytes") or 0)
        declared_max = int(model.get("max_context_length") or MIN_CONTEXT)
        max_context = min(
            declared_max,
            self.requested_max_context or declared_max,
        )
        max_context = max(MIN_CONTEXT, max_context)
        baseline = read_resource_snapshot()

        self.status("search_fast", "Finding GPU-resident fast profile", 10)
        fast_probe = self._search_profile(
            mode="fast",
            max_context=max_context,
            kv_cache="gpu",
            request_full_gpu=True,
            progress_start=10,
            progress_end=45,
        )
        self.status("benchmark_fast", "Benchmarking fast profile", 48)
        self.load_profile(
            context_length=int(fast_probe["context_length"]),
            kv_cache="gpu",
            request_full_gpu=True,
        )
        fast_benchmark = self.benchmark()

        self.status("search_deep", "Finding RAM-backed deep-context profile", 55)
        deep_probe = self._search_profile(
            mode="deep",
            max_context=max_context,
            kv_cache="ram",
            request_full_gpu=False,
            progress_start=55,
            progress_end=85,
        )
        self.status("benchmark_deep", "Benchmarking deep-context profile", 88)
        self.load_profile(
            context_length=int(deep_probe["context_length"]),
            kv_cache="ram",
            request_full_gpu=False,
        )
        deep_benchmark = self.benchmark()

        fast_profile = self._profile_result("fast", fast_probe, fast_benchmark)
        deep_profile = self._profile_result("deep", deep_probe, deep_benchmark)
        recommended = "fast"
        if (
            deep_profile["context_length"] > fast_profile["context_length"] * 1.5
            and deep_profile["generation_speed"]
            >= fast_profile["generation_speed"] * 0.85
        ):
            recommended = "deep"

        self.status("finalize", "Restoring the recommended profile", 95)
        active = fast_profile if recommended == "fast" else deep_profile
        self.load_profile(
            context_length=int(active["context_length"]),
            kv_cache=str(active["kv_cache"]),
            request_full_gpu=recommended == "fast",
        )

        return {
            "model_id": self.model_id,
            "embedding_model": self.embedding_model,
            "declared_max_context": declared_max,
            "tested_max_context": max_context,
            "gpu_target_percent": self.gpu_target_percent,
            "ram_target_percent": self.ram_target_percent,
            "resource_snapshot": baseline,
            "telemetry": {
                "gpu_usage_source": (baseline.get("gpu") or {}).get("source"),
                "ram_usage_source": (baseline.get("ram") or {}).get("source"),
                "lms_cli_available": bool(self.lms_cli),
                "actual_weight_residency_exposed_by_lmstudio": False,
            },
            "profiles": {"fast": fast_profile, "deep": deep_profile},
            "recommended_mode": recommended,
            "active_mode": recommended,
            "probes": [
                {
                    "mode": probe.get("mode"),
                    "context_length": probe.get("context_length"),
                    "fits": probe.get("fits"),
                    "reason": probe.get("reason"),
                }
                for probe in self.probes
            ],
        }


def apply_profile(
    *,
    base_url: str,
    model_id: str,
    api_key: Optional[str],
    embedding_model: Optional[str],
    profile: dict[str, Any],
) -> dict[str, Any]:
    runner = LMStudioCalibrationRunner(
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
        embedding_model=embedding_model,
        gpu_target_percent=100,
        ram_target_percent=100,
        max_context=int(profile["context_length"]),
        status_callback=lambda *args, **kwargs: None,
    )
    return runner.load_profile(
        context_length=int(profile["context_length"]),
        kv_cache=str(profile.get("kv_cache") or "gpu"),
        request_full_gpu=str(profile.get("mode") or "") == "fast",
    )
