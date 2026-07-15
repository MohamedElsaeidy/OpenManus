---
tags: [tool, editor]
type: class
source_path: app/tool/str_replace_editor.py
---

# Str Replace Editor

`StrReplaceEditor` (defined in `app/tool/str_replace_editor.py`) is a string-matching file editor. It allows the agent to modify files by declaring target blocks and replacement text.

## Core Operations
- **`view`**: Displays file content with line numbers. Restricts views to a specific line range or maximum size to avoid token overflow.
- **`str_replace`**: Replaces a uniquely matching `old_str` with `new_str` inside a specified file. Rejects updates if the target string matches multiple locations (ambiguity safety) or matches zero locations.
- **`insert`**: Inserts a new string block after a target line number or uniquely matching string block.
- **`undo`**: Reverts the last edit operation by restoring the automatic file backup.

## Links
- [[Tools MOC]]
- [[Line Edit Tool]]
- [[Apply Patch Editor]]
