---
tags: [tool, codebase]
type: class
source_path: app/tool/codebase.py
---

# Codebase Tool

`CodebaseTool` (defined in `app/tool/codebase.py`) is a codebase analysis and search tool. It helps the agent parse file layouts, look up imports, and inspect project configurations.

## Actions
- **Overview**: Maps directory structures, identifies project types, and extracts likely verification and test suite commands.
- **Search**: Uses Ripgrep and Glob helpers to find exact text matches and specific file paths across directories.

## Links
- [[Tools MOC]]
- [[Verification Gate]]
- [[REST API Surface]]
