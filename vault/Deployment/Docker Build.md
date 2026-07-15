---
tags: [deployment, docker]
type: module
source_path: Dockerfile
---

# Docker Build

The deployment container builds for OpenManus are structured around the root `Dockerfile` and `requirements.txt` configurations.

## Build Flow
- **Base Image**: Uses a Python 3.12 base configuration to package both backend FastAPI servers and Celery background workers.
- **Dependency Isolation**: Installs build systems, PostgreSQL database adapters (`psycopg2`), and Playwright dependencies to support headless web browsing.
- **Container Reuse**: The same build image (`openmanus-web:latest` / `openmanus-worker:latest`) is reused for both API execution processes and background worker processes by changing the execution entrypoint commands.

## Links
- [[Deployment MOC]]
- [[Docker Compose Stack]]
