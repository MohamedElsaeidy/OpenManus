<p align="center">
  <img src="assets/logo.jpg" width="180" alt="OpenManus"/>
</p>

English | [中文](README_zh.md) | [한국어](README_ko.md) | [日本語](README_ja.md)

# OpenManus

**A conversation-first agent runtime with persistent sandbox computers, live observability, and practical team workflows.**

This fork focuses on real daily usage: long-running conversations, isolated workspaces, runtime controls, and a UI that makes the agent’s work visible and manageable.

## Why This Repo

OpenManus here is built for people who want an agent they can actually run, inspect, and improve:

- Persistent conversations with replayable history
- Per-conversation sandbox + workspace isolation
- Live tool traces, terminal output, browser screenshots, and token counts
- Runtime controls for processes, ports, and spawned containers
- Optional RL policy scaffold compatible with OpenManus-RL workflows

## What’s Implemented

- Auth + admin bootstrap (first signup becomes admin)
- Session-based web auth and admin settings UI/API
- Conversation model with grouped tasks and follow-up continuity
- Per-conversation files under `workspace/conversations/<conversation_id>/`
- Reused sandbox computer per conversation
- Mid-task user messages to running agents
- Live SSE lifecycle stream (thoughts, tools, terminal, browser, usage)
- Process/container visibility and kill controls (with protection rules)
- Better completion artifacts and warnings (including missing PDF hints)
- OpenHands-style skill loading support
- RL integration scaffold and policy export helper

## Architecture

- Backend API: `server/api.py` (FastAPI + SSE)
- Worker runtime: `server/tasks.py` (Celery)
- Agent core: `app/agent/manus.py`, `app/agent/toolcall.py`
- LLM compatibility/runtime: `app/llm.py`
- Sandbox lifecycle: `app/sandbox/conversation.py`
- Frontend: `frontend/` (React + Vite)
- Persistence: PostgreSQL
- Event transport: Redis Streams

## Quick Start (Docker)

1. Copy config:

```bash
cp config/config.example.toml config/config.toml
```

2. Set your model endpoint in `config/config.toml`.

3. Start:

```bash
docker compose up --build
```

4. Open:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`

## Local Python Start

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
python main.py
```

## Configuration

Main file: `config/config.toml`

Important sections:

- `[llm]` model, endpoint, key, token limits
- `[sandbox]` runtime limits and network/socket access
- `[agent]` max steps and tool-call behavior
- `[rl]` optional policy integration

Example RL toggle:

```toml
[rl]
enabled = true
policy_mode = "rl"
policy_path = "research/openmanus-rl/artifacts/policy/latest/policy.md"
metadata_path = "research/openmanus-rl/artifacts/policy/latest/metadata.json"
```

## RL Scaffold

This repo includes a clean path for OpenManus-RL style integration:

- `research/openmanus-rl/`
- `app/agent/policy_loader.py`
- `scripts/export_policy.py`

Example:

```bash
python scripts/export_policy.py \
  --policy-file /path/to/policy.md \
  --model qwen3.6-35b-a3b \
  --benchmark gaia \
  --run-id exp-2026-05-14
```

## Contributing

Contributions are very welcome.

- Open an issue for bugs or feature proposals
- Keep PRs focused and easy to review
- Add verification steps in your PR description

### Maintainer Commitment

I actively monitor this repo and **I will respond to pull requests quickly**.
If your PR is blocked, tag me in the thread and I’ll help unblock it fast.

### Suggested Team Flow

1. Create feature branches from `main`
2. Open PRs early (draft PRs are welcome)
3. Merge after review + verification

## Runtime Notes

- Sandboxes are scoped to conversation lifecycle.
- Protected system processes/containers are not killable by runtime controls.
- Deleting a conversation clears associated task records, streams, and sandbox state.

## License

MIT (same as project root license).
