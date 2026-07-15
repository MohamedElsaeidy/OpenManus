---
tags: [known-issue, server]
type: known-issue
source_path: server/tasks.py
---

# Obsidian Edge Wipe on Auto-Sync

The **Obsidian Edge Wipe on Auto-Sync** describes the database deletion behavior when running workspace synchronization tasks.

## Description
- **The Issue**: Every time a background task starts or finishes, the pipeline runs the `auto_sync_obsidian_notes` routine to parse workspace `.md` updates. In earlier builds, the routine would clear and delete *all* relationship edges within a conversation before rebuilding them from scratch.
- **Outcome**: This process wiped out manually created wikilink edges or relationships imported from static vaults, limiting visualization graphs to only references found in workspace files.
- **Mitigation**: Sync operations must be modified to use diff-based updates that isolate and delete only edges sourced from auto-synced workspace files, leaving manually imported vault structure untouched.

## Links
- [[Known Issues MOC]]
- [[Obsidian Vault Sync]]
- [[Task Pipeline]]
