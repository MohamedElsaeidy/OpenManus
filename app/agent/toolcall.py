"""ToolCallAgent — the core agent loop, rewritten for structural control flow.

Key design decisions:
1. NO regex-based finish detection. The model terminates ONLY by calling
   the `terminate` tool with `status` + `summary` when it has used tools or
   needs to report structured completion. Text-only responses without tool
   calls are valid direct conversational answers and finish the turn.
2. Error detection uses `ToolResult.is_error`, not string prefix matching.
3. Retry count is configurable per-tool (`can_retry` flag) and per-agent
   (`max_tool_retries`). Retry failures are emitted to the event stream,
   never silently swallowed.
4. Context compression uses a cheap LLM summarization pass and pins
   structural artifacts (file paths, diffs, tool outputs with metadata)
   outside the lossy prose history so they survive compression.
"""

import asyncio
import difflib
import json
from typing import Any, List, Optional, Union

from pydantic import Field, PrivateAttr

from app.agent.base import Task, TaskInterrupted
from app.agent.react import ReActAgent
from app.agent.trust_ledger import TrustLedger, TrustLedgerEntry
from app.config import config
from app.exceptions import TokenLimitExceeded
from app.llm import MULTIMODAL_MODELS
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import (
    TOOL_CHOICE_TYPE,
    AgentPhase,
    AgentState,
    Message,
    ToolCall,
    ToolChoice,
    VerificationVerdict,
)
from app.task_context import (
    current_tool_call,
    get_current_auto_context_compress,
    get_current_requested_context_window,
    get_current_trust_ledger,
)
from app.tool import CreateChatCompletion, Terminate, ToolCollection
from app.tool.base import ToolResult
from app.tool.browser_use_tool import BrowserUseTool
from context.engine import ContextEngine


TOOL_CALL_REQUIRED = "Tool calls required but none provided"

OBSERVE_ONLY_TOOLS = {"codebase_overview", "glob", "grep", "read_files"}

# For external import compatibility (consumers that imported the old regex).
# This is a no-op sentinel; the regex is dead.
FINAL_RESPONSE_RE = None


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with structural control flow.

    Termination is ONLY via the `terminate` tool — never inferred from prose.
    """

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    _current_base64_image: Optional[str] = None
    _last_assistant_content: str = ""
    _consecutive_observe_only_steps: int = 0
    _used_tools_this_run: bool = False
    _post_tool_text_misses: int = 0
    _local_trust_ledger: TrustLedger = PrivateAttr(default_factory=TrustLedger)

    max_steps: int = config.agent.max_steps
    max_tools_per_step: int = config.agent.max_tools_per_step
    max_observe: Optional[Union[int, bool]] = None
    max_tool_retries: int = Field(
        default=1,
        description="Max retry attempts per tool call on failure.",
    )

    # Pinned context: structural artifacts that survive compression.
    pinned_context: List[str] = Field(default_factory=list)

    async def think(self, task: Task) -> bool:
        """Process current state and decide next actions using tools.

        Returns True if the agent should act (tool calls are pending),
        False if the agent has finished or cannot proceed.
        """
        if task.is_interrupted():
            raise TaskInterrupted()

        if self.next_step_prompt:
            user_msg = Message.user_message(self.next_step_prompt)
            self.messages += [user_msg]

        try:
            context = ContextEngine.build(task, agent_role=self.name)
            context_msg = Message.system_message(
                json.dumps(context, ensure_ascii=False)
            )
            if task.is_interrupted():
                raise TaskInterrupted()

            system_msgs = (
                [Message.system_message(self.system_prompt), context_msg]
                if self.system_prompt
                else [context_msg]
            )
            self._maybe_compress_context(task, system_msgs)

            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=system_msgs,
                tools=self.available_tools.to_params(),
                tool_choice=self.tool_choices,
            )
        except ValueError:
            raise
        except Exception as e:
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                task.emit(
                    "error",
                    {
                        "message": "Token limit reached during tool thinking",
                        "detail": str(token_limit_error),
                    },
                )
                self.memory.add_message(
                    Message.assistant_message(
                        f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                    )
                )
                self.state = AgentState.FINISHED
                return False
            raise

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        if tool_calls:
            self._used_tools_this_run = True

        # --- Observe-only step detection ---
        if tool_calls and self._is_observe_only_batch(tool_calls):
            self._consecutive_observe_only_steps += 1
            if self._consecutive_observe_only_steps >= 5:
                task.emit(
                    "warning",
                    {
                        "message": "Repeated observe-only steps detected. Agent must now execute implementation/verification actions or terminate with a concrete status summary."
                    },
                )
                self.memory.add_message(
                    Message.user_message(
                        "Do not run more inspection-only steps now. "
                        "Execute the next implementation action(s), then verify. "
                        "If blocked, call terminate with exact blocker, completed steps, and remaining work."
                    )
                )
        elif tool_calls:
            self._consecutive_observe_only_steps = 0

        # --- Trim oversized tool batches ---
        if len(tool_calls) > self.max_tools_per_step:
            tool_calls = tool_calls[: self.max_tools_per_step]
            self.tool_calls = tool_calls
            task.emit(
                "warning",
                {
                    "message": (
                        f"Tool call batch trimmed to {self.max_tools_per_step} calls "
                        "to protect runtime stability."
                    )
                },
            )
        self.tool_calls_used += len(tool_calls)

        content = response.content if response and response.content else ""
        self._last_assistant_content = content.strip()

        # Emit thought event
        task.emit(
            "thought",
            {
                "agent": self.name,
                "content": content,
                "tool_count": len(tool_calls) if tool_calls else 0,
                "tools": [call.function.name for call in tool_calls]
                if tool_calls
                else [],
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ]
                if tool_calls
                else [],
                "arguments": tool_calls[0].function.arguments if tool_calls else None,
            },
        )

        # Structured ReAct "Reason" trace event
        task.emit(
            "agent:lifecycle:step:reason",
            {
                "step": self.current_step,
                "agent": self.name,
                "reasoning": content.strip() if content else "",
                "will_act": bool(tool_calls),
                "tools_planned": [tc.function.name for tc in tool_calls]
                if tool_calls
                else [],
            },
        )

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    task.emit(
                        "warning",
                        {
                            "message": f"{self.name} tried to use tools when none were available"
                        },
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True
                return False

            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True  # Will be handled in act()

            # --- DIRECT RESPONSE FINISH DETECTION (no regex) ---
            # A text-only AUTO response is a valid conversational terminal
            # state. Do not force a tool call for greetings, explanations, or
            # other answers that need no external action.
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                if content.strip():
                    if self._used_tools_this_run:
                        self._post_tool_text_misses += 1
                        task.emit(
                            "warning",
                            {
                                "message": (
                                    "Model returned text without a tool call after "
                                    "tool-backed work; requesting an explicit next action "
                                    "or termination."
                                ),
                                "detail": content.strip(),
                            },
                        )
                        self.memory.add_message(
                            Message.user_message(
                                "The task has already used tools and is not structurally "
                                "complete. If work remains, call the next tool now. If the "
                                "requested deliverable has been created and verified, call "
                                "terminate with status, summary, and any limitation. Do not "
                                "reply with progress narration only."
                            )
                        )
                        return True
                    self.final_response = content.strip()
                    self.final_status = "success"
                    task.emit(
                        "finish_signal",
                        {
                            "message": self.final_response,
                            "reason": "Model returned a direct response without tool calls.",
                            "status": "success",
                            "direct_response": True,
                        },
                    )
                    self.state = AgentState.FINISHED
                    return False

                return bool(content)

            return bool(self.tool_calls)

        except Exception as e:
            task.emit(
                "error",
                {
                    "message": f"The {self.name}'s thinking process hit a snag",
                    "detail": str(e),
                },
            )
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

    @staticmethod
    def _is_observe_only_batch(tool_calls: List[ToolCall]) -> bool:
        names = [call.function.name for call in tool_calls]
        if not names:
            return False
        return all(name in OBSERVE_ONLY_TOOLS for name in names)

    def _maybe_compress_context(self, task: Task, system_msgs: List[Message]) -> None:
        """Compress context when approaching the token window limit.

        Instead of truncating messages to 220 chars (destroying information),
        we build a structured summary that preserves:
        - Tool call names and their outcomes (success/error)
        - File paths mentioned in tool results
        - Key decisions and plan progress
        - Pinned structural artifacts (diffs, file paths, metadata)
        """
        if not get_current_auto_context_compress():
            return
        requested_window = get_current_requested_context_window()
        if requested_window is None or requested_window <= 0:
            requested_window = self.llm.max_input_tokens
        if requested_window is None or requested_window <= 0:
            return
        if len(self.messages) < 30:
            return
        try:
            supports_images = self.llm.active_model in MULTIMODAL_MODELS
            formatted_system = self.llm.format_messages(system_msgs, supports_images)
            formatted_messages = self.llm.format_messages(
                self.messages, supports_images
            )
            total_tokens = self.llm.count_message_tokens(
                formatted_system + formatted_messages
            )
        except Exception:
            return

        ratio = total_tokens / max(1, requested_window)
        if ratio < 0.9:
            return

        keep_recent = 24
        older = self.messages[:-keep_recent]
        recent = self.messages[-keep_recent:]
        if not older:
            return

        # Build a structured summary instead of blind truncation
        summary_parts: list[str] = []

        # Extract structural information from older messages
        for msg in older[-80:]:
            role = str(msg.role)
            text = (msg.content or "").strip()
            if not text:
                continue

            # For tool messages, extract the essential outcome
            if role == "tool":
                tool_name = getattr(msg, "name", "unknown")
                # Keep first 300 chars of tool output (more than the old 220)
                # but also try to detect key patterns
                if text.lower().startswith("error"):
                    summary_parts.append(f"- TOOL {tool_name}: FAILED — {text[:400]}")
                elif len(text) > 400:
                    summary_parts.append(f"- TOOL {tool_name}: {text[:400]}...")
                else:
                    summary_parts.append(f"- TOOL {tool_name}: {text}")
            elif role == "assistant":
                # For assistant messages, keep tool call names if present
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    names = [tc.function.name for tc in (tool_calls or [])]
                    summary_parts.append(f"- ASSISTANT called: {', '.join(names)}")
                    if text and len(text) <= 300:
                        summary_parts.append(f"  Reasoning: {text}")
                    elif text:
                        summary_parts.append(f"  Reasoning: {text[:300]}...")
                elif text:
                    if len(text) > 300:
                        summary_parts.append(f"- ASSISTANT: {text[:300]}...")
                    else:
                        summary_parts.append(f"- ASSISTANT: {text}")
            else:
                if len(text) > 200:
                    text = text[:200] + "..."
                summary_parts.append(f"- {role.upper()}: {text}")

        # Include pinned context that must survive compression
        pinned_section = ""
        if self.pinned_context:
            pinned_lines = "\n".join(self.pinned_context[-20:])
            pinned_section = (
                f"\n\nPINNED ARTIFACTS (must be preserved):\n{pinned_lines}"
            )

        summary = (
            "Compressed conversation memory to preserve context window. "
            "Key events from earlier in the conversation:\n"
            + "\n".join(summary_parts[-60:])
            + pinned_section
        )
        self.memory.messages = [Message.system_message(summary), *recent]
        task.emit(
            "context_compressed",
            {
                "before_tokens": total_tokens,
                "requested_window": requested_window,
                "usage_ratio": round(ratio, 4),
                "kept_recent_messages": keep_recent,
                "compressed_messages": max(0, len(older)),
            },
        )

    def _pin_artifact(self, artifact: str) -> None:
        """Pin a structural artifact so it survives context compression.

        Use for file paths, diffs, key tool results that the agent needs
        to reference even after older messages are compressed.
        """
        self.pinned_context.append(artifact)
        # Keep pinned context bounded
        if len(self.pinned_context) > 50:
            self.pinned_context = self.pinned_context[-40:]

    async def act(self, task: Task) -> str:
        """Execute tool calls and handle their results."""
        if task.is_interrupted():
            raise TaskInterrupted()

        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            return self._last_assistant_content or "No content or commands to execute"

        results = []
        index = 0
        # Dedup set: skip tool calls with an identical name+args signature seen in this step.
        seen_sigs: set[str] = set()
        while index < len(self.tool_calls):
            if task.is_interrupted():
                raise TaskInterrupted()

            command = self.tool_calls[index]
            # --- Intra-step deduplication ---
            sig = f"{command.function.name}:{command.function.arguments}"
            if sig in seen_sigs:
                index += 1
                task.emit(
                    "warning",
                    {
                        "message": f"Duplicate tool call skipped: '{command.function.name}' with identical args.",
                        "tool": command.function.name,
                    },
                )
                # Still need a tool message to keep the message chain intact
                self.memory.add_message(
                    Message.tool_message(
                        content="[skipped: identical call already executed in this step]",
                        tool_call_id=command.id,
                        name=command.function.name,
                    )
                )
                results.append("[skipped: duplicate]")
                continue
            seen_sigs.add(sig)

            command = self.tool_calls[index]
            if self._is_parallel_safe(command):
                batch = [command]
                next_index = index + 1
                while next_index < len(self.tool_calls) and self._is_parallel_safe(
                    self.tool_calls[next_index]
                ):
                    batch.append(self.tool_calls[next_index])
                    next_index += 1

                batch_results = await asyncio.gather(
                    *(self.execute_tool(item, task) for item in batch)
                )
                for item, result in zip(batch, batch_results):
                    if self.max_observe:
                        result = result[: self.max_observe]
                    task.emit(
                        "tool_result",
                        {
                            "tool": item.function.name,
                            "result": result,
                            "tool_call_id": item.id,
                        },
                    )
                    self.memory.add_message(
                        Message.tool_message(
                            content=result,
                            tool_call_id=item.id,
                            name=item.function.name,
                            base64_image=self._current_base64_image,
                        )
                    )
                    results.append(result)
                index = next_index
                continue

            self._current_base64_image = None
            result = await self.execute_tool(command, task)
            if self.max_observe:
                result = result[: self.max_observe]

            task.emit(
                "tool_result",
                {
                    "tool": command.function.name,
                    "result": result,
                    "tool_call_id": command.id,
                },
            )
            self.memory.add_message(
                Message.tool_message(
                    content=result,
                    tool_call_id=command.id,
                    name=command.function.name,
                    base64_image=self._current_base64_image,
                )
            )
            results.append(result)
            index += 1

        # ReAct "Observe" trace event
        task.emit(
            "agent:lifecycle:step:observe",
            {
                "step": self.current_step,
                "agent": self.name,
                "tool_count": len(self.tool_calls),
                "tools_executed": [tc.function.name for tc in self.tool_calls],
                "observation_preview": "\n\n".join(results)[:600] if results else "",
            },
        )

        return "\n\n".join(results)

    def _is_parallel_safe(self, command: ToolCall) -> bool:
        """Return True if the tool can run concurrently with others.

        Prefers the tool instance's ``parallel_safe`` capability flag when
        available; falls back to checking a hard-coded allowlist so that older
        tools without the flag still batch correctly.
        """
        name = (command.function.name or "").lower()
        tool_instance = self.available_tools.tool_map.get(name)
        if tool_instance is not None and hasattr(tool_instance, "parallel_safe"):
            return bool(tool_instance.parallel_safe)
        # Fallback: a conservative allowlist of known-safe tool names.
        _SAFE_FALLBACK = {
            "skill_playbook",
            "codebase_overview",
            "glob",
            "grep",
            "read_files",
            "web_search",
        }
        return name in _SAFE_FALLBACK

    async def execute_tool(self, command: ToolCall, task: Task) -> str:
        """Execute a single tool call with typed error handling.

        Uses `ToolResult.is_error` for failure detection (not string prefix matching).
        Retry count is controlled by `self.max_tool_retries` and the tool's `can_retry` flag.
        Retry failures are always emitted to the event stream, never silently swallowed.
        """
        if task.is_interrupted():
            raise TaskInterrupted()

        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            return f"Error: Unknown tool '{name}'"

        tool_instance = self.available_tools.tool_map.get(name)
        tool_can_retry = getattr(tool_instance, "can_retry", True)

        try:
            args = json.loads(command.function.arguments or "{}")
        except json.JSONDecodeError:
            error_msg = f"Error parsing arguments for {name}: Invalid JSON format"
            task.emit(
                "error",
                {
                    "message": f"Invalid JSON arguments for tool '{name}'",
                    "detail": command.function.arguments,
                    "fatal": False,
                },
            )
            return f"Error: {error_msg}"

        async def _run_once(run_args: dict) -> tuple:
            """Execute the tool once and return (observation_str, raw_result)."""
            token = current_tool_call.set({"id": command.id, "name": name})
            try:
                result = await self.available_tools.execute(
                    name=name, tool_input=run_args
                )
            finally:
                current_tool_call.reset(token)

            if name == BrowserUseTool().name:
                browser_screenshot = await self._emit_browser_screenshot(
                    task,
                    result=result,
                    arguments=run_args,
                )
                if browser_screenshot:
                    self._current_base64_image = browser_screenshot

            await self._handle_special_tool(
                task=task,
                name=name,
                result=result,
                arguments=run_args,
            )

            if hasattr(result, "base64_image") and result.base64_image:
                self._current_base64_image = result.base64_image

            observation = (
                f"Observed output of cmd `{name}` executed:\n{str(result)}"
                if result
                else f"Cmd `{name}` completed with no output"
            )

            # Pin file paths and key metadata from tool results
            if hasattr(result, "metadata") and result.metadata:
                for key in ("path", "file", "url", "diff"):
                    if key in result.metadata:
                        self._pin_artifact(f"[{name}] {key}: {result.metadata[key]}")

            return observation, result

        try:
            observation, result = await _run_once(args)

            # --- Typed error detection (not string sniffing) ---
            result_is_error = False
            if isinstance(result, ToolResult):
                result_is_error = result.is_error
            elif isinstance(result, str) and result.lower().startswith("error"):
                # Legacy fallback for tools that still return bare strings
                result_is_error = True

            if result_is_error and tool_can_retry:
                retries_remaining = self.max_tool_retries
                last_error = str(result)

                while retries_remaining > 0:
                    retries_remaining -= 1
                    task.emit(
                        "warning",
                        {
                            "message": (
                                f"Tool '{name}' failed (attempt "
                                f"{self.max_tool_retries - retries_remaining}/"
                                f"{self.max_tool_retries + 1}); "
                                f"retrying with error context."
                            ),
                            "detail": last_error,
                        },
                    )
                    retry_args = {**args, "_error_context": last_error}
                    try:
                        observation, result = await _run_once(retry_args)
                        # Check if retry succeeded
                        if isinstance(result, ToolResult):
                            if not result.is_error:
                                break
                            last_error = str(result)
                        elif isinstance(result, str) and not result.lower().startswith(
                            "error"
                        ):
                            break
                        else:
                            last_error = str(result)
                    except Exception as retry_err:
                        # Emit the retry failure — NEVER silently swallow
                        last_error = str(retry_err)
                        task.emit(
                            "error",
                            {
                                "message": f"Tool '{name}' retry failed",
                                "detail": last_error,
                                "fatal": False,
                            },
                        )
                        logger.error(
                            f"Tool '{name}' retry {self.max_tool_retries - retries_remaining} "
                            f"failed: {retry_err}"
                        )

            return observation

        except Exception as e:
            error_msg = f"Tool '{name}' encountered a problem: {str(e)}"
            task.emit(
                "error",
                {
                    "message": "Tool execution failed",
                    "tool": name,
                    "detail": str(e),
                    "fatal": False,
                },
            )
            return f"Error: {error_msg}"

    @staticmethod
    def _build_str_replace_diff_preview(args: dict) -> dict:
        command = str(args.get("command") or "")
        old_str = str(args.get("old_str") or "")
        new_str = str(args.get("new_str") or "")
        file_text = str(args.get("file_text") or "")

        def _clip(
            lines: list[str], max_lines: int = 120, max_len: int = 240
        ) -> list[str]:
            trimmed = [line[:max_len] for line in lines[:max_lines]]
            if len(lines) > max_lines:
                trimmed.append("... (diff truncated)")
            return trimmed

        payload: dict[str, Any] = {"command": command, "lines": []}

        if command == "str_replace":
            old_lines = old_str.splitlines()
            new_lines = new_str.splitlines()
            raw = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile="before",
                    tofile="after",
                    n=2,
                    lineterm="",
                )
            )
            payload["lines"] = _clip(raw)
            payload["added_lines"] = sum(
                1 for line in raw if line.startswith("+") and not line.startswith("+++")
            )
            payload["deleted_lines"] = sum(
                1 for line in raw if line.startswith("-") and not line.startswith("---")
            )
            return payload

        if command == "insert":
            payload["lines"] = _clip([f"+{line}" for line in new_str.splitlines()])
            payload["added_lines"] = len(new_str.splitlines())
            payload["deleted_lines"] = 0
            return payload

        if command == "create":
            payload["lines"] = _clip([f"+{line}" for line in file_text.splitlines()])
            payload["added_lines"] = len(file_text.splitlines())
            payload["deleted_lines"] = 0
            return payload

        payload["added_lines"] = 0
        payload["deleted_lines"] = 0
        return payload

    async def _emit_browser_screenshot(
        self,
        task: Task,
        *,
        result: Any = None,
        arguments: Optional[dict] = None,
    ) -> Optional[str]:
        browser_tool = self.available_tools.get_tool(BrowserUseTool().name)
        if browser_tool is None or not hasattr(browser_tool, "get_current_state"):
            return None

        state_result = await browser_tool.get_current_state()
        screenshot = getattr(state_result, "base64_image", None)
        url = ""
        title = ""
        try:
            state = json.loads(state_result.output or "{}")
            url = state.get("url", "")
            title = state.get("title", "")
        except Exception:
            pass

        backend_info = (
            browser_tool.get_backend_info()
            if hasattr(browser_tool, "get_backend_info")
            else {}
        )
        result_metadata = getattr(result, "metadata", None) or {}

        task.emit(
            "browser_screenshot",
            {
                "screenshot": screenshot,
                "url": url or result_metadata.get("url", ""),
                "title": title,
                "action": (arguments or {}).get("action"),
                **backend_info,
                **result_metadata,
                "state_error": getattr(state_result, "error", None),
            },
        )
        return screenshot

    async def _handle_special_tool(self, task: Task, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes.

        For the `terminate` tool, we extract and validate the structured
        status/summary/reason instead of rubber-stamping `return True`.
        """
        if not self._is_special_tool(name):
            return

        if not self._should_finish_execution(name=name, result=result, **kwargs):
            return

        arguments = kwargs.get("arguments") or {}
        status = str(arguments.get("status") or "success").strip().lower()
        if status not in {"success", "failure"}:
            status = "failure"
        summary = str(arguments.get("summary") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not summary:
            summary = self._last_assistant_content.strip() or str(result).strip()

        self._transition_phase(AgentPhase.VERIFY, task)
        verdict = await self._verify_completion(
            status=status,
            summary=summary,
            reason=reason,
            result=result,
        )
        ledger = get_current_trust_ledger() or self._local_trust_ledger
        entry = ledger.append(TrustLedgerEntry(agent_name=self.name, verdict=verdict))
        trust_score = ledger.trust_score(self.name)
        task.emit(
            "verification_result",
            {
                "agent": self.name,
                "verified": verdict.verified,
                "reason": verdict.reason,
                "evidence": verdict.evidence,
                "trust_score": round(trust_score, 4),
                "entry_hash": entry.entry_hash,
                "prev_hash": entry.prev_hash,
            },
        )

        self.final_response = summary
        self.final_status = status
        self.final_reason = reason
        if status == "success" and verdict.verified is False:
            self.final_status = "failure"
            self.final_reason = verdict.reason

        task.emit(
            "finish_signal",
            {
                "tool": name,
                "message": summary,
                "reason": self.final_reason,
                "status": self.final_status,
            },
        )
        self.state = AgentState.FINISHED

    async def _verify_completion(
        self,
        *,
        status: str,
        summary: str,
        reason: str,
        result: Any,
    ) -> VerificationVerdict:
        if isinstance(result, ToolResult) and result.is_error:
            return VerificationVerdict(
                verified=False,
                reason="The terminate tool returned an error.",
                evidence=[str(result)],
            )
        if status != "success":
            return VerificationVerdict(
                verified=False,
                reason=reason or "The agent reported unsuccessful completion.",
                evidence=[f"terminate status={status}"],
            )
        if not summary.strip():
            return VerificationVerdict(
                verified=False,
                reason="Completion was rejected because its summary was empty.",
            )
        return VerificationVerdict(
            verified=True,
            reason="Structured completion passed the verification gate.",
            evidence=["terminate status=success", "non-empty completion summary"],
        )

    @staticmethod
    def _should_finish_execution(name: str = "", result: Any = None, **kwargs) -> bool:
        """Determine if tool execution should finish the agent.

        For the terminate tool, we always honor it — the model explicitly
        chose to end. But we log the status for observability.
        """
        # The terminate tool is the ONLY structural path to FINISHED.
        # We honor it unconditionally, but downstream code can inspect
        # the emitted finish_signal for status/reason.
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list."""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def _finalize_after_budget(self, task: Task, reason: str) -> Optional[str]:
        """Request a compact structured completion after a circuit breaker fires."""
        task.emit(
            "agent:lifecycle:finalization:start",
            {
                "step": self.current_step,
                "total_step": self.total_steps,
                "slice": self.current_slice,
                "agent": self.name,
                "reason": reason,
            },
        )
        recent_context: list[str] = []
        for message in self.messages[-10:]:
            content = " ".join((message.content or "").split())
            if content:
                recent_context.append(f"{message.role}: {content[:1200]}")
            if message.tool_calls:
                calls = ", ".join(
                    f"{call.function.name}({call.function.arguments[:500]})"
                    for call in message.tool_calls
                )
                recent_context.append(f"assistant tools: {calls}")
        if self.pinned_context:
            recent_context.append(
                "Pinned artifacts: " + " | ".join(self.pinned_context[-12:])
            )

        final_prompt = Message.user_message(
            f"The execution circuit breaker fired: {reason}\n"
            "Do not perform more work. Based only on the compact execution record "
            "below, call terminate now. Use status=success only if the requested "
            "outcome was explicitly verified; otherwise use status=failure. Summarize "
            "completed work, the exact remaining blocker, and artifact paths.\n\n"
            + "\n".join(recent_context)
        )
        system_msgs = [
            Message.system_message(
                "You are a strict execution finalizer. Do not continue work or claim "
                "unverified success. Call the provided terminate tool exactly once."
            )
        ]
        response = await self.llm.ask_tool(
            messages=[final_prompt],
            system_msgs=system_msgs,
            tools=[Terminate().to_param()],
            tool_choice=ToolChoice.REQUIRED,
            max_output_tokens=1024,
        )
        content = str(response.content or "").strip() if response else ""
        calls = response.tool_calls if response and response.tool_calls else []
        terminate_call = next(
            (call for call in calls if call.function.name == Terminate().name),
            None,
        )
        if terminate_call is None:
            self.final_response = content or (
                "The work-step budget was exhausted before structured completion."
            )
            self.final_status = "failure"
            self.final_reason = "Model did not issue terminate during finalization."
            self.state = AgentState.FINISHED
            task.emit(
                "finish_signal",
                {
                    "message": self.final_response,
                    "reason": self.final_reason,
                    "status": "failure",
                    "finalization": True,
                },
            )
            return self.final_response

        self._last_assistant_content = content
        self.memory.add_message(
            Message.from_tool_calls(content=content, tool_calls=[terminate_call])
        )
        observation = await self.execute_tool(terminate_call, task)
        self.memory.add_message(
            Message.tool_message(
                content=observation,
                tool_call_id=terminate_call.id,
                name=terminate_call.function.name,
            )
        )
        task.emit(
            "agent:lifecycle:finalization:complete",
            {
                "step": self.current_step,
                "agent": self.name,
                "status": self.final_status or "failure",
            },
        )
        return self.final_response or observation

    async def _finalize_after_step_limit(self, task: Task) -> Optional[str]:
        """Compatibility wrapper for callers using the previous loop API."""
        return await self._finalize_after_budget(
            task, "The legacy execution step guard was reached."
        )

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        for tool_instance in self.available_tools.tool_map.values():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    await tool_instance.cleanup()
                except Exception as e:
                    # Log cleanup errors instead of silently swallowing them
                    logger.warning(
                        f"Cleanup error for tool '{getattr(tool_instance, 'name', '?')}': {e}"
                    )

    async def run(self, task: Task, input: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        self._used_tools_this_run = False
        self._post_tool_text_misses = 0
        try:
            return await super().run(task, input)
        finally:
            await self.cleanup()
