---
tags: [known-issue, deployment]
type: known-issue
source_path: docker-compose.yml
---

# Docker Compose Hardcoded Path

The **Docker Compose Hardcoded Path** issue relates to system configuration variables set in the environment declarations of the compose stack.

## Description
- **The Issue**: Inside `docker-compose.yml`, the environment variable `OPENMANUS_HOST_WORKSPACE_ROOT` is set as `OPENMANUS_HOST_WORKSPACE_ROOT=${PWD}/workspace`.
- **Consequences**: If a developer starts the stack outside the project directory root, the `${PWD}` variable maps to the current active shell path rather than the project directory, leading to mount failures. Furthermore, if host root paths are absolute or hardcoded to specific developer home folders (e.g. `/home/user/workspace`), the configuration cannot be shared or run across different target machines without editing the file.
- **Remediation**: Use relative workspace directories or load host path configurations via local `.env` files.

## Links
- [[Known Issues MOC]]
- [[Docker Compose Stack]]
