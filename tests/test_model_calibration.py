from server.model_calibration import parse_lms_estimate, parse_nvidia_smi, resource_fit


def test_parse_lms_estimate_extracts_memory_and_confidence():
    estimate = parse_lms_estimate(
        """
        Estimated GPU Memory: 27.40 GiB
        Estimated Total Memory: 29.15 GiB
        Confidence: LOW
        This model may be loaded with the selected configuration.
        """
    )

    assert estimate["estimated_gpu_bytes"] == int(27.40 * 1024**3)
    assert estimate["estimated_total_bytes"] == int(29.15 * 1024**3)
    assert estimate["confidence"] == "low"
    assert estimate["guardrails_allow"] is True


def test_parse_nvidia_smi_aggregates_valid_device_rows():
    devices = parse_nvidia_smi(
        "0, NVIDIA RTX 2080 Ti, 11264, 10240, 1024\n"
        "1, NVIDIA RTX A5000, 24564, 23000, 1564\n"
    )

    assert [device["index"] for device in devices] == [0, 1]
    assert (
        sum(device["total_bytes"] for device in devices) == (11264 + 24564) * 1024**2
    )
    assert (
        sum(device["used_bytes"] for device in devices) == (10240 + 23000) * 1024**2
    )


def test_resource_fit_enforces_gpu_then_ram_limits():
    snapshot = {
        "gpu": {"used_percent": 97.1},
        "ram": {"used_percent": 70.0},
    }
    fits, reason = resource_fit(
        snapshot,
        gpu_target_percent=97.0,
        ram_target_percent=85.0,
    )
    assert fits is False
    assert "GPU use" in reason

    snapshot["gpu"]["used_percent"] = 96.5
    snapshot["ram"]["used_percent"] = 86.0
    fits, reason = resource_fit(
        snapshot,
        gpu_target_percent=97.0,
        ram_target_percent=85.0,
    )
    assert fits is False
    assert "RAM use" in reason

    snapshot["ram"]["used_percent"] = 84.0
    fits, reason = resource_fit(
        snapshot,
        gpu_target_percent=97.0,
        ram_target_percent=85.0,
    )
    assert fits is True
    assert reason == "within resource targets"
