"""Integration tests for ToolCallAgent end-to-end loop with mock LLM responses.

Tests the full ReAct step() cycle, phase transitions, direct responses,
structured termination, and tool retries.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.base import Task
from app.agent.execution_policy import ExecutionPolicy
from app.agent.toolcall import ToolCallAgent
from app.schema import AgentPhase, AgentState, Function, ToolCall


class MockLLMResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class TestAgentLoopIntegration:
    @pytest.fixture
    def task(self):
        return Task(id="test_task_1")

    @pytest.fixture
    def agent(self):
        return ToolCallAgent(name="test_agent")

    @pytest.mark.asyncio
    async def test_full_step_cycle_to_terminate(self, agent, task):
        """Test a normal tool call step followed by termination."""
        # Turn 1: model calls a tool (e.g. bash)
        tc1 = ToolCall(
            id="call_1",
            function=Function(name="bash", arguments='{"command": "echo hello"}'),
        )
        # Turn 2: model calls terminate
        tc2 = ToolCall(
            id="call_2",
            function=Function(
                name="terminate",
                arguments='{"status": "success", "summary": "All done!"}',
            ),
        )

        mock_ask = AsyncMock(
            side_effect=[
                MockLLMResponse(content="Running command...", tool_calls=[tc1]),
                MockLLMResponse(content="Finishing up...", tool_calls=[tc2]),
            ]
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100):
            # Step 1: PLAN -> ACT -> OBSERVE
            await agent.step(task)
            assert agent.phase == AgentPhase.OBSERVE
            assert agent.state == AgentState.IDLE

            # Step 2: PLAN -> ACT -> OBSERVE -> DONE (due to terminate tool)
            await agent.step(task)
            assert agent.state == AgentState.FINISHED
            assert agent.final_response == "All done!"
            assert agent.final_status == "success"

    @pytest.mark.asyncio
    async def test_text_only_response_finishes_directly(self, agent, task):
        """A conversational response is a successful one-step terminal state."""
        mock_ask = AsyncMock(return_value=MockLLMResponse(content="Hello there!"))

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100):
            result = await agent.step(task)
            assert agent.state == AgentState.FINISHED
            assert agent.final_response == "Hello there!"
            assert agent.final_status == "success"
            assert result == "Hello there!"
            assert agent.phase == AgentPhase.DONE

    @pytest.mark.asyncio
    async def test_text_only_progress_after_tools_does_not_finish(self, agent, task):
        """Progress narration after a tool call must not become the final answer."""
        tc = ToolCall(
            id="call_1",
            function=Function(name="bash", arguments='{"command": "echo research"}'),
        )
        mock_ask = AsyncMock(
            side_effect=[
                MockLLMResponse(content="Researching...", tool_calls=[tc]),
                MockLLMResponse(content="Good progress. Let me fetch more sources."),
            ]
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100):
            await agent.step(task)
            result = await agent.step(task)

        assert agent.state != AgentState.FINISHED
        assert agent.final_response is None
        assert result == "Good progress. Let me fetch more sources."
        assert any(
            "call the next tool now" in (message.content or "")
            for message in agent.messages
        )

    @pytest.mark.asyncio
    async def test_termination_on_last_work_step_is_preserved(self, agent, task):
        terminate = ToolCall(
            id="terminate_last",
            function=Function(
                name="terminate",
                arguments='{"status":"success","summary":"Artifact verified"}',
            ),
        )
        agent.max_steps = 1
        mock_ask = AsyncMock(
            return_value=MockLLMResponse(content="Done", tool_calls=[terminate])
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100), patch(
            "app.agent.base.SANDBOX_CLIENT.cleanup", new=AsyncMock()
        ):
            result = await agent.run(task, "create an artifact")

        assert result == "Artifact verified"
        assert agent.final_status == "success"
        assert agent.final_reason == ""

    @pytest.mark.asyncio
    async def test_step_limit_gets_termination_only_finalization(self, agent, task):
        work = ToolCall(
            id="work_last",
            function=Function(name="bash", arguments='{"command":"echo done"}'),
        )
        terminate = ToolCall(
            id="terminate_finalize",
            function=Function(
                name="terminate",
                arguments=(
                    '{"status":"success","summary":"artifact.tex and artifact.pdf '
                    'were created and verified"}'
                ),
            ),
        )
        agent.max_steps = 1
        agent.execution_policy = agent.execution_policy.model_copy(
            update={"slice_steps": 1, "max_continuations": 0}
        )
        mock_ask = AsyncMock(
            side_effect=[
                MockLLMResponse(content="Verifying", tool_calls=[work]),
                MockLLMResponse(content="Finalizing", tool_calls=[terminate]),
            ]
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100), patch(
            "app.agent.base.SANDBOX_CLIENT.cleanup", new=AsyncMock()
        ):
            result = await agent.run(task, "create an artifact")

        assert mock_ask.await_count == 2
        assert len(mock_ask.await_args_list[1].kwargs["tools"]) == 1
        assert result == "artifact.tex and artifact.pdf were created and verified"
        assert agent.final_status == "success"

    @pytest.mark.asyncio
    async def test_slice_boundary_resumes_with_full_tools(self, agent, task):
        work = ToolCall(
            id="work_first_slice",
            function=Function(name="bash", arguments='{"command":"echo partial"}'),
        )
        terminate = ToolCall(
            id="terminate_second_slice",
            function=Function(
                name="terminate",
                arguments='{"status":"success","summary":"continued and verified"}',
            ),
        )
        agent.max_steps = 1
        agent.execution_policy = agent.execution_policy.model_copy(
            update={"slice_steps": 1, "max_continuations": 1}
        )
        mock_ask = AsyncMock(
            side_effect=[
                MockLLMResponse(content="Starting work", tool_calls=[work]),
                MockLLMResponse(content="Done", tool_calls=[terminate]),
            ]
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(agent.llm, "count_message_tokens", return_value=100), patch(
            "app.agent.base.SANDBOX_CLIENT.cleanup", new=AsyncMock()
        ):
            result = await agent.run(task, "complete a multi-pass task")

        assert result == "continued and verified"
        assert agent.current_slice == 2
        assert agent.total_steps == 2
        assert len(mock_ask.await_args_list[1].kwargs["tools"]) > 1

    @pytest.mark.asyncio
    async def test_hard_token_budget_uses_finalization_only(self, agent, task):
        from app.task_context import current_execution_usage

        terminate = ToolCall(
            id="terminate_budget",
            function=Function(
                name="terminate",
                arguments=(
                    '{"status":"failure","summary":"partial work preserved",'
                    '"reason":"token budget reached"}'
                ),
            ),
        )
        agent.execution_policy = ExecutionPolicy.for_mode("fast").model_copy(
            update={"token_budget": 1}
        )
        usage_token = current_execution_usage.set(
            {"input": 1, "completion": 0, "total": 1}
        )
        mock_ask = AsyncMock(
            return_value=MockLLMResponse(content="Finalizing", tool_calls=[terminate])
        )
        try:
            with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
                agent.llm, "format_messages", return_value=[]
            ), patch.object(agent.llm, "count_message_tokens", return_value=100), patch(
                "app.agent.base.SANDBOX_CLIENT.cleanup", new=AsyncMock()
            ):
                result = await agent.run(task, "large task")
        finally:
            current_execution_usage.reset(usage_token)

        assert result == "partial work preserved"
        assert agent.final_status == "failure"
        assert agent.total_steps == 0
        assert len(mock_ask.await_args.kwargs["tools"]) == 1
        assert len(mock_ask.await_args.kwargs["messages"]) == 1
        assert mock_ask.await_args.kwargs["max_output_tokens"] == 1024

    def test_projected_step_cost_preserves_finalization_reserve(self, agent):
        from app.task_context import current_execution_usage

        agent.execution_policy = ExecutionPolicy.for_mode("balanced").model_copy(
            update={"token_budget": 320_000}
        )
        agent.total_steps = 10
        agent.last_step_token_cost = 51_151
        usage_token = current_execution_usage.set(
            {"input": 258_705, "completion": 35_773, "total": 294_478}
        )
        try:
            reason = agent._hard_budget_reason(0.0)
        finally:
            current_execution_usage.reset(usage_token)

        assert reason is not None
        assert "25,522 tokens left" in reason
        assert "51,151" in reason

    @pytest.mark.asyncio
    async def test_tool_retry_on_error(self, agent, task):
        """Test that execute_tool retries when a tool returns ToolResult.is_error."""
        from app.tool.base import ToolResult

        tc = ToolCall(
            id="call_retry",
            function=Function(name="mock_tool", arguments='{"param": "failing"}'),
        )

        mock_tool_instance = AsyncMock()
        mock_tool_instance.can_retry = True
        mock_tool_instance.parallel_safe = False
        agent.available_tools.tool_map["mock_tool"] = mock_tool_instance

        mock_ask = AsyncMock(
            return_value=MockLLMResponse(
                content="Running failing tool", tool_calls=[tc]
            )
        )

        # Mock available_tools.execute to fail on attempt 1, succeed on attempt 2
        mock_exec = AsyncMock(
            side_effect=[
                ToolResult(error="Command failed with exit code 1", exit_code=1),
                ToolResult(output="Success on retry!", exit_code=0),
            ]
        )

        with patch.object(agent.llm, "ask_tool", mock_ask), patch.object(
            agent.llm, "format_messages", return_value=[]
        ), patch.object(
            agent.llm, "count_message_tokens", return_value=100
        ), patch.object(
            agent.available_tools, "execute", mock_exec
        ):
            obs = await agent.step(task)
            # execute should have been called twice (1 initial + 1 retry)
            assert mock_exec.call_count == 2
            # The second call should include _error_context in tool_input
            second_call_args = mock_exec.call_args_list[1].kwargs.get("tool_input", {})
            assert "_error_context" in second_call_args
            assert (
                "Command failed with exit code 1" in second_call_args["_error_context"]
            )
            assert "Success on retry!" in obs
