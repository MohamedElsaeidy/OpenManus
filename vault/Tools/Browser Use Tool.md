---
tags: [tool, browser]
type: class
source_path: app/tool/browser_use_tool.py
---

# Browser Use Tool

`BrowserUseTool` (defined in `app/tool/browser_use_tool.py`) is a web browser controller integration based on the Playwright framework.

## Capabilities
- **Browser Automation**: Navigates to URLs, clicks links/buttons, types text into input fields, scrolls web pages, and takes screenshots.
- **State Capture**: Extracts active tab titles, HTML elements, and coordinates of interactive widgets.
- **Multimodal Feedback**: Passes current screen captures to the Browser Agent helper, providing the LLM with visual representations of loaded pages.

## Links
- [[Tools MOC]]
- [[Browser Agent]]
- [[Web Search Tool]]
