---
tags: [agent-core, moc]
type: moc
---

# Agent Core MOC

This Map of Content (MOC) indexes the core agent execution models, lifecycle loops, state transitions, and specific agent personas that drive OpenManus.

## Architecture and Execution Loops
- [[ReAct Loop]] — The core Reason-Act-Observe pattern driving agent step cycles.
- [[Tool Call Agent]] — Base execution model for tool-augmented agents.
- [[Verification Gate]] — Final validation checkpoint before task finalization.
- [[Context Compression]] — Automatic summarizing logic to prevent token overflow.

## Specialized Agent Personas
- [[Manus Agent]] — The orchestrator assembling 17 default tools and MCP capabilities.
- [[Planner Agent]] — Pure planning agent producing structured step lists.
- [[SWE Agent]] — Code editing persona for repository engineering tasks.
- [[Browser Agent]] — Web interaction persona powered by browser-use integrations.

## Links
- [[00 Home]]
- [[ReAct Loop]]
- [[Tool Call Agent]]
- [[Verification Gate]]
- [[Context Compression]]
- [[Manus Agent]]
- [[Planner Agent]]
- [[SWE Agent]]
- [[Browser Agent]]
