---
tags: [server, obsidian]
type: feature
source_path: server/tasks.py
---

# Obsidian Vault Sync

The **Obsidian Vault Sync** mechanism (defined in `server/tasks.py` and `server/api.py`) automatically maps workspace markdown files into structured Obsidian vaults within conversation sessions.

## Execution Flow
1. **File Scanning**: Scans the workspace directory for `*.md` files (ignoring files in `.git`, `node_modules`, and virtual environments).
2. **Metadata Extraction**: Extracts YAML frontmatter, headers, tags (using regex patterns), and file content, storing them in the `obsidian_notes` table (defined in `server/models.py`).
3. **Wikilink Parsing**: Searches note contents for `[[wikilink]]` references to build relational edges between notes.
4. **Resolution Strategy**: Resolves references based on a prioritized lookup order:
   - *Path-stem match*: Strips `.md` from stored note paths to match path-qualified references (e.g. `[[folder/Note]]` matches `folder/Note.md`).
   - *Basename match*: Matches filename stems (e.g. `[[Note]]` matches `folder/Note.md`).
   - *Title match*: Matches note titles, ignoring duplicate titles to avoid incorrect edge connections.
5. **Database Edges**: Stores resolved note relationships in the `obsidian_edges` table.

## Links
- [[Server MOC]]
- [[Task Pipeline]]
- [[Obsidian Wikilink Resolution Bug]]
- [[Obsidian Edge Wipe on Auto-Sync]]
