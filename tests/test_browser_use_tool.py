from unittest.mock import AsyncMock

import pytest
from browser_use import BrowserConfig

from app.tool.browser_use_tool import BrowserUseTool, ManagedCloakBrowser


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
