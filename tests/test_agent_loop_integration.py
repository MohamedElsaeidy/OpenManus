"""Integration tests for ToolCallAgent end-to-end loop with mock LLM responses.

Tests the full ReAct step() cycle, phase transitions, direct responses,
structured termination, and tool retries.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.base import Task
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
