import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from browser_use import BrowserConfig

from app.agent.toolcall import ToolCallAgent
from app.tool.base import ToolResult
from app.tool.browser_use_tool import BrowserUseTool, ManagedCloakBrowser
from core.task import Task


def test_browser_startup_failure_is_not_retried_by_agent():
    assert BrowserUseTool().can_retry is False


@pytest.mark.asyncio
async def test_managed_cloak_launch_is_headless_cpu_only():
    launch = AsyncMock(return_value=object())
    playwright = type(
        "PlaywrightStub",
        (),
        {"chromium": type("ChromiumStub", (), {"launch": launch})()},
    )()
    browser = ManagedCloakBrowser(
        BrowserConfig(
            headless=True,
            chrome_instance_path="/opt/cloak/chrome",
        )
    )

    result = await browser._setup_browser_with_instance(playwright)

    assert result is launch.return_value
    kwargs = launch.await_args.kwargs
    assert kwargs["executable_path"] == "/opt/cloak/chrome"
    assert kwargs["headless"] is True
    assert "--disable-gpu" in kwargs["args"]
    assert "--no-sandbox" in kwargs["args"]


def test_dom_text_normalization_is_deterministic():
    content = "  Heading  \n\n\nFirst\t row\n  Second   row  "

    assert BrowserUseTool._normalize_dom_text(content) == (
        "Heading\n\nFirst row\nSecond row"
    )


@pytest.mark.asyncio
async def test_extract_content_returns_dom_text_when_model_has_no_tool_call():
    tool = BrowserUseTool(llm=None)
    tool._backend = "cloakbrowser"
    tool.llm = SimpleNamespace(
        ask_tool=AsyncMock(return_value=SimpleNamespace(tool_calls=[]))
    )
    page = SimpleNamespace(
        url="https://example.test/page",
        inner_text=AsyncMock(return_value="Heading\nUseful rendered evidence"),
    )

    result = await tool._extract_page_content(page, "find evidence", 2000)

    assert not result.is_error
    assert "Useful rendered evidence" in result.output
    assert result.metadata["browser_backend"] == "cloakbrowser"
    assert result.metadata["extraction_method"] == "dom_text_fallback"
    assert result.metadata["extraction_fallback_reason"] == (
        "model returned no tool call or direct text"
    )
    call = tool.llm.ask_tool.await_args
    assert call.kwargs["timeout"] == 120
    assert call.kwargs["max_output_tokens"] == 8192


@pytest.mark.asyncio
async def test_extract_content_returns_dom_text_when_model_times_out():
    tool = BrowserUseTool(llm=None)
    tool.llm = SimpleNamespace(ask_tool=AsyncMock(side_effect=asyncio.TimeoutError))
    page = SimpleNamespace(
        url="https://example.test/slow",
        inner_text=AsyncMock(return_value="Rendered content survives model failure"),
    )

    result = await tool._extract_page_content(page, "summarize", 2000)

    assert "Rendered content survives model failure" in result.output
    assert result.metadata["extraction_method"] == "dom_text_fallback"
    assert "model timeout" in result.metadata["extraction_fallback_reason"]


@pytest.mark.asyncio
async def test_extract_content_uses_valid_model_tool_result():
    arguments = json.dumps(
        {"extracted_content": {"text": "Model-selected evidence", "metadata": {}}}
    )
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                function=SimpleNamespace(
                    name="extract_content",
                    arguments=arguments,
                )
            )
        ]
    )
    tool = BrowserUseTool(llm=None)
    tool._backend = "cloakbrowser"
    tool.llm = SimpleNamespace(ask_tool=AsyncMock(return_value=response))
    page = SimpleNamespace(
        url="https://example.test/page",
        inner_text=AsyncMock(return_value="Full rendered page content"),
    )

    result = await tool._extract_page_content(page, "extract", 2000)

    assert result.output == "Model-selected evidence"
    assert result.metadata["extraction_method"] == "model"
    assert result.metadata["browser_backend"] == "cloakbrowser"


@pytest.mark.asyncio
async def test_extract_content_accepts_direct_model_text():
    tool = BrowserUseTool(llm=None)
    tool.llm = SimpleNamespace(
        ask_tool=AsyncMock(
            return_value=SimpleNamespace(
                tool_calls=[],
                content="Direct extraction from the local model",
            )
        )
    )
    page = SimpleNamespace(
        url="https://example.test/page",
        inner_text=AsyncMock(return_value="Full rendered page content"),
    )

    result = await tool._extract_page_content(page, "extract", 32000)

    assert result.output == "Direct extraction from the local model"
    assert result.metadata["extraction_method"] == "model_direct_text"


@pytest.mark.asyncio
async def test_cloak_startup_failure_falls_back_to_playwright(monkeypatch):
    from app.tool import browser_use_tool as browser_module

    cloak_browser = SimpleNamespace(
        new_context=AsyncMock(side_effect=RuntimeError("cloak failed")),
        close=AsyncMock(),
    )
    page = SimpleNamespace()
    context = SimpleNamespace(get_current_page=AsyncMock(return_value=page))
    stock_browser = SimpleNamespace(
        new_context=AsyncMock(return_value=context),
        close=AsyncMock(),
    )
    monkeypatch.setattr(browser_module, "_get_cloak_binary_path", lambda: "/cloak")
    monkeypatch.setattr(
        browser_module, "ManagedCloakBrowser", lambda browser_config: cloak_browser
    )
    monkeypatch.setattr(
        browser_module, "BrowserUseBrowser", lambda browser_config: stock_browser
    )
    monkeypatch.setattr(browser_module, "DomService", lambda current_page: object())
    tool = BrowserUseTool(llm=None)

    result = await tool._ensure_browser_initialized()

    assert result is context
    assert tool.get_backend_info()["browser_backend"] == "playwright_chromium"
    assert tool.get_backend_info()["browser_fallback"] is True
    assert "cloak failed" in tool.get_backend_info()["browser_fallback_reason"]
    cloak_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_screenshot_event_contains_backend_and_result_metadata():
    state = ToolResult(
        output=json.dumps({"url": "https://example.test", "title": "Example"}),
        base64_image="encoded-image",
    )
    browser_tool = SimpleNamespace(
        get_current_state=AsyncMock(return_value=state),
        get_backend_info=lambda: {
            "browser_backend": "cloakbrowser",
            "browser_fallback": False,
        },
    )
    agent = SimpleNamespace(
        available_tools=SimpleNamespace(get_tool=lambda name: browser_tool)
    )
    task = Task(id="browser-event")

    screenshot = await ToolCallAgent._emit_browser_screenshot(
        agent,
        task,
        result=ToolResult(
            metadata={
                "extraction_method": "dom_text_fallback",
                "extraction_fallback_reason": "model timeout after 120s",
            }
        ),
        arguments={"action": "extract_content"},
    )
    event = await task.event_queue.get()

    assert screenshot == "encoded-image"
    assert event["type"] == "browser_screenshot"
    assert event["data"]["browser_backend"] == "cloakbrowser"
    assert event["data"]["action"] == "extract_content"
    assert event["data"]["extraction_method"] == "dom_text_fallback"
