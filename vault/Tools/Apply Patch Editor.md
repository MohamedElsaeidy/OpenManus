---
tags: [tool, editor]
type: class
source_path: app/tool/apply_patch_editor.py
---

# Apply Patch Editor

`ApplyPatchEditor` (defined in `app/tool/apply_patch_editor.py`) is an editor designed to apply multi-file unified diff blocks atomically. It enables the model to update multiple parts of a file or project in a single step.

## Characteristics
- **Diff Parsing**: Accepts standard unified diff patches containing `+++` (new file), `---` (old file), and `@@` line headers.
- **Atomic Operations**: If a patch fails to apply to any single target file (due to conflicting code context or incorrect line offsets), the entire transaction is rolled back, preventing partial edits.
- **Conflict Handling**: Uses git-like merge heuristics to resolve small line offset drifts when applying changes.

## Links
- [[Tools MOC]]
- [[Line Edit Tool]]
- [[Str Replace Editor]]
