---
tags: [known-issue, moc]
type: moc
---

# Known Issues MOC

This Map of Content (MOC) indexes the documented security risks, architectural bottlenecks, and synchronization bugs identified in the current OpenManus codebase.

## System & Synchronization Bugs
- [[Obsidian Wikilink Resolution Bug]] — Ambiguity and failure matching path-qualified notes.
- [[Obsidian Edge Wipe on Auto-Sync]] — Manual graph relationships wiped by automatic sync runs.

## Deployment & Security Risks
- [[Docker Compose Hardcoded Path]] — Host workspace path configurations.
- [[Docker Socket Mount Risk]] — Host system access risks from container sandboxes.

## Architecture Bottlenecks
- [[REST API Monolith]] — Large API server file structure combining unrelated logic paths.

## Links
- [[00 Home]]
- [[Obsidian Wikilink Resolution Bug]]
- [[Obsidian Edge Wipe on Auto-Sync]]
- [[Docker Compose Hardcoded Path]]
- [[Docker Socket Mount Risk]]
- [[REST API Monolith]]
