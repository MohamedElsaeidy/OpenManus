---
tags: [known-issue, deployment]
type: known-issue
source_path: docker-compose.yml
---

# Docker Socket Mount Risk

The **Docker Socket Mount Risk** represents a major security vulnerability in the deployment layout of OpenManus.

## Description
- **The Issue**: In the `docker-compose.yml` manifest, the host's Docker socket is mounted directly inside the application container sandboxes:
  `/var/run/docker.sock:/var/run/docker.sock`
- **Consequences**: If the agent executes untrusted code from files or downloads malicious packages inside the workspace, it has full root access to the host's Docker daemon. This allows the process to control host containers, list private images, spin up privileged containers, and potentially escalate privileges to run arbitrary commands on the host machine.
- **Remediation**: Avoid raw socket mounts; route sandbox creation requests through a secure proxy server or gRPC runtime API wrapper.

## Links
- [[Known Issues MOC]]
- [[Docker Compose Stack]]
- [[Bash Tool]]
