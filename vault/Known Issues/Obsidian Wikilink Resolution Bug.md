---
tags: [known-issue, server]
type: known-issue
source_path: server/tasks.py
---

# Obsidian Wikilink Resolution Bug

The **Obsidian Wikilink Resolution Bug** describes issues in note resolution when managing path-qualified wikilink edges.

## Description
- **Resolution Failures**: In previous versions, path-qualified wikilink strings (such as `[[projects/Overview]]`) failed to match the database records for target files stored with full extensions (e.g. `projects/Overview.md`).
- **Collision Collapses**: When multiple files across different directories shared identical stems or titles (e.g. `Overview.md` inside both `projects/` and `archive/`), title-only lookups collided, silently resolving edges to whichever file was scanned last.
- **Resolution Order**: The system now utilizes a strict resolution order (path-stem > basename > exact path > unambiguous title) to resolve matching targets. Links are skipped if duplicate titles make target selection ambiguous.

## Links
- [[Known Issues MOC]]
- [[Obsidian Vault Sync]]
