---
tags: [deployment, docker]
type: module
source_path: docker-compose.yml
---

# Docker Compose Stack

The **Docker Compose Stack** manages local deployment orchestration for OpenManus, defining multiple services in `docker-compose.yml` to support concurrent execution.

## Services Map
1. **`frontend`**: Serves the Vite React client, mapping container port `5173` to host port `3000`.
2. **`web`**: Runs the FastAPI application with Uvicorn on port `8000`. It coordinates DB access and schedules celery tasks.
3. **`worker`**: Runs the Celery task queue runner to execute long-running agent loops in the background.
4. **`postgres`**: Relational store for configurations, conversations, and obsidian vault graphs.
5. **`redis`**: Key-value cache acting as the celery task broker and SSE broadcast backend.

## Volume Mounts & Env Paths
- **Workspace Volume**: Mounts `./workspace:/app/workspace` to provide agents with direct file system access.
- **Docker Socket Mount**: Mounts the host daemon (`/var/run/docker.sock:/var/run/docker.sock`) to allow containers to spin up sub-sandboxes.

## Links
- [[Deployment MOC]]
- [[Docker Build]]
- [[Docker Compose Hardcoded Path]]
- [[Docker Socket Mount Risk]]
