import asyncio
import base64
import json
import re
from typing import Any, Generic, Optional, TypeVar

from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from pydantic import Field, PrivateAttr, field_validator
from pydantic_core.core_schema import ValidationInfo

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.web_search import WebSearch


BROWSER_STARTUP_TIMEOUT_SECONDS = 25
NAVIGATION_TIMEOUT_SECONDS = 30
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 120
DEFAULT_EXTRACTION_CONTENT_LENGTH = 32000
EXTRACTION_MAX_OUTPUT_TOKENS = 8192
DOM_FALLBACK_CONTENT_LENGTH = 8000


def _get_cloak_binary_path() -> Optional[str]:
    """Return the CloakBrowser stealth Chromium binary path if available.

    Downloads the binary on first call (no-op if already cached).
    Returns None on any error so the caller falls back to stock Playwright.
    """
    try:
        import cloakbrowser

        info = cloakbrowser.binary_info()
        if info.get("installed"):
            return info["binary_path"]
        # Binary not yet cached — download it (happens once, stored in ~/.cloakbrowser/)
        logger.info("CloakBrowser: binary not cached, downloading now…")
        path = cloakbrowser.ensure_binary()
        logger.info(f"CloakBrowser: stealth binary ready at {path}")
        return path
    except Exception as exc:
        logger.warning(
            f"CloakBrowser unavailable, falling back to stock Playwright: {exc}"
        )
        return None


_BROWSER_DESCRIPTION = """\
A powerful browser automation tool that allows interaction with web pages through various actions.
* This tool provides commands for controlling a browser session, navigating web pages, and extracting information
* It maintains state across calls, keeping the browser session alive until explicitly closed
* Use this when you need to browse websites, fill forms, click buttons, extract content, or perform web searches
* Each action requires specific parameters as defined in the tool's dependencies

Key capabilities include:
* Navigation: Go to specific URLs, go back, search the web, or refresh pages
* Interaction: Click elements, input text, select from dropdowns, send keyboard commands
* Scrolling: Scroll up/down by pixel amount or scroll to specific text
* Content extraction: Extract and analyze content from web pages based on specific goals
* Tab management: Switch between tabs, open new tabs, or close tabs

Note: When using element indices, refer to the numbered elements shown in the current browser state.
"""

Context = TypeVar("Context")


class ManagedCloakBrowser(BrowserUseBrowser):
    """Launch CloakBrowser through Playwright so its process is owned and closed."""

    async def _setup_browser_with_instance(self, playwright):
        return await playwright.chromium.launch(
            executable_path=self.config.chrome_instance_path,
            headless=self.config.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                *self.disable_security_args,
                *self.config.extra_chromium_args,
            ],
            proxy=self.config.proxy,
        )


class BrowserUseTool(BaseTool, Generic[Context]):
    name: str = "browser_use"
    description: str = _BROWSER_DESCRIPTION
    can_retry: bool = False
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_url",
                    "click_element",
                    "input_text",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "send_keys",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "go_back",
                    "refresh",
                    "web_search",
                    "wait",
                    "extract_content",
                    "switch_tab",
                    "open_tab",
                    "close_tab",
                ],
                "description": "The browser action to perform",
            },
            "url": {
                "type": "string",
                "description": "URL for 'go_to_url' or 'open_tab' actions",
            },
            "index": {
                "type": "integer",
                "description": "Element index for 'click_element', 'input_text', 'get_dropdown_options', or 'select_dropdown_option' actions",
            },
            "text": {
                "type": "string",
                "description": "Text for 'input_text', 'scroll_to_text', or 'select_dropdown_option' actions",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "Pixels to scroll (positive for down, negative for up) for 'scroll_down' or 'scroll_up' actions",
            },
            "tab_id": {
                "type": "integer",
                "description": "Tab ID for 'switch_tab' action",
            },
            "query": {
                "type": "string",
                "description": "Search query for 'web_search' action",
            },
            "goal": {
                "type": "string",
                "description": "Extraction goal for 'extract_content' action",
            },
            "keys": {
                "type": "string",
                "description": "Keys to send for 'send_keys' action",
            },
            "seconds": {
                "type": "integer",
                "description": "Seconds to wait for 'wait' action",
            },
        },
        "required": ["action"],
        "dependencies": {
            "go_to_url": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "switch_tab": ["tab_id"],
            "open_tab": ["url"],
            "scroll_down": ["scroll_amount"],
            "scroll_up": ["scroll_amount"],
            "scroll_to_text": ["text"],
            "send_keys": ["keys"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "go_back": [],
            "refresh": [],
            "web_search": ["query"],
            "wait": ["seconds"],
            "extract_content": ["goal"],
        },
    }

    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    browser: Optional[BrowserUseBrowser] = Field(default=None, exclude=True)
    context: Optional[BrowserContext] = Field(default=None, exclude=True)
    dom_service: Optional[DomService] = Field(default=None, exclude=True)
    web_search_tool: WebSearch = Field(default_factory=WebSearch, exclude=True)
    _initialization_error: Optional[str] = PrivateAttr(default=None)
    _backend: str = PrivateAttr(default="uninitialized")
    _backend_executable: Optional[str] = PrivateAttr(default=None)
    _fallback_reason: Optional[str] = PrivateAttr(default=None)

    # Context for generic functionality
    tool_context: Optional[Context] = Field(default=None, exclude=True)

    llm: Optional[LLM] = Field(default_factory=LLM)

    @field_validator("parameters", mode="before")
    def validate_parameters(cls, v: dict, info: ValidationInfo) -> dict:
        if not v:
            raise ValueError("Parameters cannot be empty")
        return v

    def get_backend_info(self) -> dict[str, Any]:
        """Return stable runtime metadata for browser lifecycle events."""
        return {
            "browser_backend": self._backend,
            "browser_executable_path": self._backend_executable,
            "browser_fallback": bool(self._fallback_reason),
            "browser_fallback_reason": self._fallback_reason,
        }

    async def _new_context(
        self, browser: BrowserUseBrowser, context_config: BrowserContextConfig
    ) -> BrowserContext:
        return await asyncio.wait_for(
            browser.new_context(context_config),
            timeout=BROWSER_STARTUP_TIMEOUT_SECONDS,
        )

    async def _close_browser_instance(self) -> None:
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception as exc:
                logger.debug(f"Browser cleanup after startup failure failed: {exc}")
        self.browser = None
        self.context = None
        self.dom_service = None

    async def _ensure_browser_initialized(self) -> BrowserContext:
        """Initialize the configured browser and recover from Cloak startup failure."""
        if self._initialization_error:
            raise RuntimeError(self._initialization_error)

        browser_config_kwargs: dict[str, Any] = {
            "headless": True,
            "disable_security": True,
        }
        browser_class = BrowserUseBrowser

        if self.browser is None:
            if config.browser_config:
                from browser_use.browser.browser import ProxySettings

                if config.browser_config.proxy and config.browser_config.proxy.server:
                    browser_config_kwargs["proxy"] = ProxySettings(
                        server=config.browser_config.proxy.server,
                        username=config.browser_config.proxy.username,
                        password=config.browser_config.proxy.password,
                    )

                browser_attrs = [
                    "headless",
                    "disable_security",
                    "extra_chromium_args",
                    "chrome_instance_path",
                    "wss_url",
                    "cdp_url",
                ]

                for attr in browser_attrs:
                    value = getattr(config.browser_config, attr, None)
                    if value is not None:
                        if not isinstance(value, list) or value:
                            browser_config_kwargs[attr] = value

            if "wss_url" in browser_config_kwargs:
                self._backend = "remote_websocket"
            elif "cdp_url" in browser_config_kwargs:
                self._backend = "remote_cdp"
            elif "chrome_instance_path" in browser_config_kwargs:
                self._backend = "custom_chromium"
                self._backend_executable = browser_config_kwargs["chrome_instance_path"]
            else:
                cloak_path = _get_cloak_binary_path()
                if cloak_path:
                    browser_class = ManagedCloakBrowser
                    browser_config_kwargs["chrome_instance_path"] = cloak_path
                    self._backend = "cloakbrowser"
                    self._backend_executable = cloak_path
                    logger.info(
                        "BrowserUseTool: using CloakBrowser stealth binary at "
                        f"{cloak_path}"
                    )
                else:
                    self._backend = "playwright_chromium"
                    logger.info(
                        "BrowserUseTool: CloakBrowser not available, using stock Playwright Chromium"
                    )

            self.browser = browser_class(BrowserConfig(**browser_config_kwargs))

        if self.context is None:
            context_config = BrowserContextConfig()
            if (
                config.browser_config
                and hasattr(config.browser_config, "new_context_config")
                and config.browser_config.new_context_config
            ):
                context_config = config.browser_config.new_context_config

            try:
                self.context = await self._new_context(self.browser, context_config)
                self.dom_service = DomService(await self.context.get_current_page())
            except Exception as exc:
                if self._backend == "cloakbrowser":
                    self._fallback_reason = f"CloakBrowser startup failed: {exc}"
                    logger.warning(
                        f"{self._fallback_reason}; retrying with stock Playwright "
                        "Chromium"
                    )
                    await self._close_browser_instance()
                    fallback_kwargs = dict(browser_config_kwargs)
                    fallback_kwargs.pop("chrome_instance_path", None)
                    self._backend = "playwright_chromium"
                    self._backend_executable = None
                    self.browser = BrowserUseBrowser(BrowserConfig(**fallback_kwargs))
                    try:
                        self.context = await self._new_context(
                            self.browser, context_config
                        )
                        self.dom_service = DomService(
                            await self.context.get_current_page()
                        )
                    except Exception as fallback_exc:
                        await self._close_browser_instance()
                        self._initialization_error = (
                            "Browser initialization failed for CloakBrowser and stock "
                            f"Playwright Chromium: {fallback_exc}"
                        )
                        raise RuntimeError(self._initialization_error) from fallback_exc
                else:
                    backend = self._backend.replace("_", " ")
                    await self._close_browser_instance()
                    self._initialization_error = (
                        f"Browser initialization failed for {backend}: {exc}"
                    )
                    raise RuntimeError(self._initialization_error) from exc

        return self.context

    @staticmethod
    def _normalize_dom_text(content: str) -> str:
        lines: list[str] = []
        previous_blank = False
        for raw_line in content.splitlines():
            line = re.sub(r"[\t\x0b\x0c\r ]+", " ", raw_line).strip()
            if not line:
                if lines and not previous_blank:
                    lines.append("")
                previous_blank = True
                continue
            lines.append(line)
            previous_blank = False
        return "\n".join(lines).strip()

    async def _read_page_content(self, page) -> str:
        """Read rendered text first, then degrade to HTML-to-markdown."""
        try:
            content = self._normalize_dom_text(await page.inner_text("body"))
        except Exception:
            content = ""

        if content:
            return content

        try:
            import markdownify

            return self._normalize_dom_text(
                markdownify.markdownify(await page.content())
            )
        except Exception:
            return ""

    async def _navigate_page(self, page, url: str) -> Optional[int]:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=NAVIGATION_TIMEOUT_SECONDS * 1000,
        )
        return response.status if response is not None else None

    def _dom_fallback_result(
        self,
        *,
        page,
        content: str,
        goal: str,
        reason: str,
    ) -> ToolResult:
        limit = max(
            DOM_FALLBACK_CONTENT_LENGTH,
            int(
                getattr(
                    config.browser_config,
                    "max_content_length",
                    DEFAULT_EXTRACTION_CONTENT_LENGTH,
                )
            ),
        )
        excerpt = content[:limit].rstrip()
        truncated = len(content) > len(excerpt)
        suffix = "\n\n[DOM text truncated]" if truncated else ""
        backend = self.get_backend_info()
        return ToolResult(
            output=(
                "Extracted rendered DOM text "
                f"(deterministic fallback: {reason}).\n"
                f"Source: {page.url}\n"
                f"Goal: {goal}\n\n{excerpt}{suffix}"
            ),
            metadata={
                **backend,
                "url": page.url,
                "extraction_method": "dom_text_fallback",
                "extraction_fallback_reason": reason,
                "content_characters": len(content),
                "returned_characters": len(excerpt),
                "truncated": truncated,
            },
        )

    async def _extract_page_content(
        self, page, goal: str, max_content_length: int
    ) -> ToolResult:
        content = await self._read_page_content(page)
        if not content:
            return ToolResult(
                error=(
                    f"Page at {page.url} returned no readable content. The page may "
                    "require authentication, be rate-limiting the browser, or still "
                    "be rendering."
                ),
                metadata={**self.get_backend_info(), "url": page.url},
            )

        prompt = f"""Extract information from the rendered page for the stated goal.
Return the result through the required extract_content tool. Preserve names,
identifiers, links, and numerical details that support the answer.

Goal: {goal}

Rendered page text:
{content[:max_content_length]}
"""
        extraction_function = {
            "type": "function",
            "function": {
                "name": "extract_content",
                "description": "Return information extracted from rendered page text",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "extracted_content": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "metadata": {"type": "object"},
                            },
                            "required": ["text"],
                        }
                    },
                    "required": ["extracted_content"],
                },
            },
        }

        if self.llm is None:
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason="model unavailable",
            )

        extraction_timeout = max(
            1,
            int(
                getattr(
                    config.browser_config,
                    "extraction_timeout_seconds",
                    DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
                )
            ),
        )

        try:
            response = await asyncio.wait_for(
                self.llm.ask_tool(
                    [{"role": "user", "content": prompt}],
                    system_msgs=[
                        {
                            "role": "system",
                            "content": (
                                "You extract evidence from supplied web page text."
                            ),
                        }
                    ],
                    timeout=extraction_timeout,
                    tools=[extraction_function],
                    tool_choice="required",
                    max_output_tokens=EXTRACTION_MAX_OUTPUT_TOKENS,
                ),
                timeout=extraction_timeout + 1,
            )
        except asyncio.TimeoutError:
            logger.warning("Browser content extraction timed out; returning DOM text")
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason=f"model timeout after {extraction_timeout}s",
            )
        except Exception as exc:
            logger.warning(
                f"Browser content extraction failed; returning DOM text: {exc}"
            )
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason=f"model error: {type(exc).__name__}",
            )

        if not response:
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason="model returned no response",
            )

        if not response.tool_calls:
            direct_text = str(getattr(response, "content", "") or "").strip()
            if direct_text:
                return ToolResult(
                    output=direct_text,
                    metadata={
                        **self.get_backend_info(),
                        "url": page.url,
                        "extraction_method": "model_direct_text",
                        "content_characters": len(content),
                    },
                )
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason="model returned no tool call or direct text",
            )

        try:
            tool_call = next(
                call
                for call in response.tool_calls
                if call.function.name == "extract_content"
            )
            arguments = json.loads(tool_call.function.arguments or "{}")
            extracted = arguments.get("extracted_content") or {}
            extracted_text = str(extracted.get("text") or "").strip()
            if not extracted_text:
                raise ValueError("empty extracted_content.text")
        except (StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._dom_fallback_result(
                page=page,
                content=content,
                goal=goal,
                reason=f"invalid model tool result: {exc}",
            )

        return ToolResult(
            output=extracted_text,
            metadata={
                **self.get_backend_info(),
                "url": page.url,
                "extraction_method": "model",
                "content_characters": len(content),
            },
        )

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        """
        Execute a specified browser action.

        Args:
            action: The browser action to perform
            url: URL for navigation or new tab
            index: Element index for click or input actions
            text: Text for input action or search query
            scroll_amount: Pixels to scroll for scroll action
            tab_id: Tab ID for switch_tab action
            query: Search query for Google search
            goal: Extraction goal for content extraction
            keys: Keys to send for keyboard actions
            seconds: Seconds to wait
            **kwargs: Additional arguments

        Returns:
            ToolResult with the action's output or error
        """
        async with self.lock:
            try:
                context = await self._ensure_browser_initialized()

                # Get max content length from config
                max_content_length = getattr(
                    config.browser_config,
                    "max_content_length",
                    DOM_FALLBACK_CONTENT_LENGTH,
                )

                # Navigation actions
                if action == "go_to_url":
                    if not url:
                        return ToolResult(
                            error="URL is required for 'go_to_url' action"
                        )
                    page = await context.get_current_page()
                    status = await self._navigate_page(page, url)
                    return ToolResult(
                        output=f"Navigated to {url}",
                        metadata={
                            **self.get_backend_info(),
                            "url": page.url,
                            "http_status": status,
                        },
                    )

                elif action == "go_back":
                    await context.go_back()
                    page = await context.get_current_page()
                    return ToolResult(
                        output="Navigated back",
                        metadata={**self.get_backend_info(), "url": page.url},
                    )

                elif action == "refresh":
                    await context.refresh_page()
                    page = await context.get_current_page()
                    return ToolResult(
                        output="Refreshed current page",
                        metadata={**self.get_backend_info(), "url": page.url},
                    )

                elif action == "web_search":
                    if not query:
                        return ToolResult(
                            error="Query is required for 'web_search' action"
                        )
                    # Execute the web search and return results directly without browser navigation
                    search_response = await self.web_search_tool.execute(
                        query=query, fetch_content=True, num_results=3
                    )
                    # If search failed or returned no results, return the error directly
                    if search_response.error or not search_response.results:
                        return ToolResult(
                            error=search_response.error
                            or f"No search results found for query: {query}"
                        )
                    # Navigate to the first search result
                    first_search_result = search_response.results[0]
                    url_to_navigate = first_search_result.url

                    page = await context.get_current_page()
                    await self._navigate_page(page, url_to_navigate)

                    return search_response

                # Element interaction actions
                elif action == "click_element":
                    if index is None:
                        return ToolResult(
                            error="Index is required for 'click_element' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    download_path = await context._click_element_node(element)
                    output = f"Clicked element at index {index}"
                    if download_path:
                        output += f" - Downloaded file to {download_path}"
                    return ToolResult(output=output)

                elif action == "input_text":
                    if index is None or not text:
                        return ToolResult(
                            error="Index and text are required for 'input_text' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    await context._input_text_element_node(element, text)
                    return ToolResult(
                        output=f"Input '{text}' into element at index {index}"
                    )

                elif action == "scroll_down" or action == "scroll_up":
                    direction = 1 if action == "scroll_down" else -1
                    amount = (
                        scroll_amount
                        if scroll_amount is not None
                        else context.config.browser_window_size["height"]
                    )
                    await context.execute_javascript(
                        f"window.scrollBy(0, {direction * amount});"
                    )
                    return ToolResult(
                        output=f"Scrolled {'down' if direction > 0 else 'up'} by {amount} pixels"
                    )

                elif action == "scroll_to_text":
                    if not text:
                        return ToolResult(
                            error="Text is required for 'scroll_to_text' action"
                        )
                    page = await context.get_current_page()
                    try:
                        locator = page.get_by_text(text, exact=False)
                        await locator.scroll_into_view_if_needed()
                        return ToolResult(output=f"Scrolled to text: '{text}'")
                    except Exception as e:
                        return ToolResult(error=f"Failed to scroll to text: {str(e)}")

                elif action == "send_keys":
                    if not keys:
                        return ToolResult(
                            error="Keys are required for 'send_keys' action"
                        )
                    page = await context.get_current_page()
                    await page.keyboard.press(keys)
                    return ToolResult(output=f"Sent keys: {keys}")

                elif action == "get_dropdown_options":
                    if index is None:
                        return ToolResult(
                            error="Index is required for 'get_dropdown_options' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    page = await context.get_current_page()
                    options = await page.evaluate(
                        """
                        (xpath) => {
                            const select = document.evaluate(xpath, document, null,
                                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                            if (!select) return null;
                            return Array.from(select.options).map(opt => ({
                                text: opt.text,
                                value: opt.value,
                                index: opt.index
                            }));
                        }
                    """,
                        element.xpath,
                    )
                    return ToolResult(output=f"Dropdown options: {options}")

                elif action == "select_dropdown_option":
                    if index is None or not text:
                        return ToolResult(
                            error="Index and text are required for 'select_dropdown_option' action"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"Element with index {index} not found")
                    page = await context.get_current_page()
                    await page.select_option(element.xpath, label=text)
                    return ToolResult(
                        output=f"Selected option '{text}' from dropdown at index {index}"
                    )

                # Content extraction actions
                elif action == "extract_content":
                    if not goal:
                        return ToolResult(
                            error="Goal is required for 'extract_content' action"
                        )

                    page = await context.get_current_page()
                    return await self._extract_page_content(
                        page, goal, max_content_length
                    )

                # Tab management actions
                elif action == "switch_tab":
                    if tab_id is None:
                        return ToolResult(
                            error="Tab ID is required for 'switch_tab' action"
                        )
                    await context.switch_to_tab(tab_id)
                    page = await context.get_current_page()
                    await page.wait_for_load_state()
                    return ToolResult(output=f"Switched to tab {tab_id}")

                elif action == "open_tab":
                    if not url:
                        return ToolResult(error="URL is required for 'open_tab' action")
                    await context.create_new_tab(url)
                    return ToolResult(output=f"Opened new tab with {url}")

                elif action == "close_tab":
                    await context.close_current_tab()
                    return ToolResult(output="Closed current tab")

                # Utility actions
                elif action == "wait":
                    requested_wait = seconds if seconds is not None else 3
                    seconds_to_wait = min(max(requested_wait, 0), 60)
                    await asyncio.sleep(seconds_to_wait)
                    return ToolResult(output=f"Waited for {seconds_to_wait} seconds")

                else:
                    return ToolResult(error=f"Unknown action: {action}")

            except Exception as e:
                return ToolResult(
                    error=f"Browser action '{action}' failed: {str(e)}",
                    metadata=self.get_backend_info(),
                )

    async def get_current_state(
        self, context: Optional[BrowserContext] = None
    ) -> ToolResult:
        """
        Get the current browser state as a ToolResult.
        If context is not provided, uses self.context.
        """
        try:
            # Use provided context or fall back to self.context
            ctx = context or self.context
            if not ctx:
                return ToolResult(error="Browser context not initialized")

            state = await ctx.get_state()

            # Create a viewport_info dictionary if it doesn't exist
            viewport_height = 0
            if hasattr(state, "viewport_info") and state.viewport_info:
                viewport_height = state.viewport_info.height
            elif hasattr(ctx, "config") and hasattr(ctx.config, "browser_window_size"):
                viewport_height = ctx.config.browser_window_size.get("height", 0)

            # Take a screenshot for the state
            page = await ctx.get_current_page()

            await page.bring_to_front()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception as exc:
                logger.debug(f"Browser state captured before load settled: {exc}")

            screenshot = await page.screenshot(
                full_page=False,
                animations="disabled",
                type="jpeg",
                quality=75,
                timeout=10000,
            )

            screenshot = base64.b64encode(screenshot).decode("utf-8")

            # Build the state info with all required fields
            state_info = {
                "url": state.url,
                "title": state.title,
                "tabs": [tab.model_dump() for tab in state.tabs],
                "help": "[0], [1], [2], etc., represent clickable indices corresponding to the elements listed. Clicking on these indices will navigate to or interact with the respective content behind them.",
                "interactive_elements": (
                    state.element_tree.clickable_elements_to_string()
                    if state.element_tree
                    else ""
                ),
                "scroll_info": {
                    "pixels_above": getattr(state, "pixels_above", 0),
                    "pixels_below": getattr(state, "pixels_below", 0),
                    "total_height": getattr(state, "pixels_above", 0)
                    + getattr(state, "pixels_below", 0)
                    + viewport_height,
                },
                "viewport_height": viewport_height,
                **self.get_backend_info(),
            }

            return ToolResult(
                output=json.dumps(state_info, indent=4, ensure_ascii=False),
                base64_image=screenshot,
                metadata=self.get_backend_info(),
            )
        except Exception as e:
            return ToolResult(error=f"Failed to get browser state: {str(e)}")

    async def cleanup(self):
        """Clean up browser resources."""
        async with self.lock:
            if self.context is not None:
                await self.context.close()
                self.context = None
                self.dom_service = None
            if self.browser is not None:
                await self.browser.close()
                self.browser = None
            self._initialization_error = None
            self._backend = "uninitialized"
            self._backend_executable = None
            self._fallback_reason = None

    @classmethod
    def create_with_context(cls, context: Context) -> "BrowserUseTool[Context]":
        """Factory method to create a BrowserUseTool with a specific context."""
        tool = cls()
        tool.tool_context = context
        return tool
