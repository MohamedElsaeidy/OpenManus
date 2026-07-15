---
tags: [agent-core, browser]
type: class
source_path: app/agent/browser.py
---

# Browser Agent

`BrowserAgent` (defined in `app/agent/browser.py`) is a specialized web automation agent designed to navigate and interact with websites. It is powered by browser-use library integrations.

## Core Operations
- **Restricted Tooling**: It only utilizes two tools: `BrowserUseTool` and `Terminate`.
- **Browser Context Helper**: Coordinates state collection from the browser:
  - Fetches the active URL, page titles, pixels above/below the fold, and tab lists.
  - Formats `NEXT_STEP_PROMPT` using active web metrics.
  - Automatically captures base64 screenshots and appends them to message history so the model can visually evaluate page layouts.
- **Resource Cleanup**: Overrides `cleanup` to release active browser contexts and shutdown headless chrome/playwright environments.

## Links
- [[Agent Core MOC]]
- [[Tool Call Agent]]
- [[Browser Use Tool]]
- [[Terminate Tool]]
---
