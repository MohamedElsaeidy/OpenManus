---
tags: [tool, editor]
type: class
source_path: app/tool/line_edit.py
---

# Line Edit Tool

`LineEdit` (defined in `app/tool/line_edit.py`) is a line-number-based editor. It provides direct, line-indexed modifications without needing string matching algorithms.

## Editing Model
- **Targeting**: Specify a file path and a range of lines (`start_line` to `end_line`, 1-indexed inclusive).
- **Validation**: Requires the agent to provide the exact `old_content` present within the line range before replacing it with `new_content`. If the provided content does not match the file contents exactly, the editor rejects the edit to avoid corruption.
- **Safety**: Automatically creates backup files before making changes, allowing undo operations if changes break tests.

## Links
- [[Tools MOC]]
- [[Str Replace Editor]]
- [[Apply Patch Editor]]
