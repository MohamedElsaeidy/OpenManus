from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from openmanus_cli import __version__
from openmanus_cli.deployment import (
    DeploymentError,
    install_release,
    release_tag_for_version,
)


DEFAULT_DIRECTORY = "openmanus-deploy"
OVERRIDE_FILE = "docker-compose.openmanus.yml"


def _deployment_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not (path / "docker-compose.yml").is_file():
        raise DeploymentError(f"No OpenManus deployment found at {path}")
    return path


def _compose_command(path: Path, *arguments: str) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(path)]
    command.extend(["--file", str(path / "docker-compose.yml")])
    override = path / OVERRIDE_FILE
    if override.is_file():
        command.extend(["--file", str(override)])
    command.extend(arguments)
    return command


def _run_compose(path: Path, *arguments: str) -> int:
    command = _compose_command(path, *arguments)
    environment = os.environ.copy()
    environment["PWD"] = str(path)
    try:
        return subprocess.run(
            command,
            cwd=path,
            env=environment,
            check=False,
        ).returncode
    except FileNotFoundError as exc:
        raise DeploymentError("Docker is not installed or is not on PATH") from exc


def _check_docker(*, verbose: bool) -> bool:
    if shutil.which("docker") is None:
        if verbose:
            print("[missing] Docker executable")
        return False

    checks = (
        ("Docker Compose", ["docker", "compose", "version"]),
        ("Docker daemon", ["docker", "info", "--format", "{{.ServerVersion}}"]),
    )
    healthy = True
    for label, command in checks:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode == 0:
            if verbose:
                detail = result.stdout.strip().splitlines()[0]
                print(f"[ok] {label}: {detail}")
        else:
            healthy = False
            if verbose:
                detail = result.stderr.strip() or result.stdout.strip() or "unavailable"
                print(f"[failed] {label}: {detail}")
    return healthy


def _initialize(path: Path, release: str) -> Path:
    target, assets, digest = install_release(path, release)
    print(f"Initialized OpenManus {assets.tag} in {target}")
    print(f"Verified SHA-256: {digest}")
    print(f"Configuration: {target / 'config' / 'config.toml'}")
    return target


def _default_release() -> str:
    return release_tag_for_version(__version__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openmanus",
        description="Install and manage an OpenManus Docker deployment.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="Check Docker deployment prerequisites")

    init_parser = subparsers.add_parser(
        "init", help="Download and initialize a production deployment"
    )
    init_parser.add_argument("path", nargs="?", default=DEFAULT_DIRECTORY)
    init_parser.add_argument("--release", default=_default_release())

    deploy_parser = subparsers.add_parser(
        "deploy", help="Initialize and start OpenManus"
    )
    deploy_parser.add_argument("path", nargs="?", default=DEFAULT_DIRECTORY)
    deploy_parser.add_argument("--release", default=_default_release())
    deploy_parser.add_argument("--no-build", action="store_true")

    up_parser = subparsers.add_parser("up", help="Start an initialized deployment")
    up_parser.add_argument("path", nargs="?", default=".")
    up_parser.add_argument("--no-build", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show deployment services")
    status_parser.add_argument("path", nargs="?", default=".")

    logs_parser = subparsers.add_parser("logs", help="Show deployment logs")
    logs_parser.add_argument("path", nargs="?", default=".")
    logs_parser.add_argument("services", nargs="*")
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument("--tail", type=int, default=200)

    down_parser = subparsers.add_parser("down", help="Stop a deployment")
    down_parser.add_argument("path", nargs="?", default=".")
    down_parser.add_argument(
        "--volumes",
        action="store_true",
        help="Also delete PostgreSQL and other named volumes",
    )
    return parser


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "doctor":
        return 0 if _check_docker(verbose=True) else 1
    if args.command == "init":
        _initialize(Path(args.path), args.release)
        return 0
    if args.command == "deploy":
        target = Path(args.path).expanduser().resolve()
        if not (target / "docker-compose.yml").is_file():
            target = _initialize(target, args.release)
        if not _check_docker(verbose=True):
            raise DeploymentError("Docker prerequisites are not ready")
        compose_args = ["up", "--detach"]
        if not args.no_build:
            compose_args.append("--build")
        result = _run_compose(target, *compose_args)
        if result == 0:
            print("OpenManus is starting at http://localhost:3000")
        return result

    target = _deployment_path(args.path)
    if args.command == "up":
        compose_args = ["up", "--detach"]
        if not args.no_build:
            compose_args.append("--build")
        return _run_compose(target, *compose_args)
    if args.command == "status":
        return _run_compose(target, "ps")
    if args.command == "logs":
        compose_args = ["logs", "--tail", str(max(args.tail, 0))]
        if args.follow:
            compose_args.append("--follow")
        compose_args.extend(args.services)
        return _run_compose(target, *compose_args)
    if args.command == "down":
        compose_args = ["down", "--remove-orphans"]
        if args.volumes:
            compose_args.append("--volumes")
        return _run_compose(target, *compose_args)
    raise DeploymentError(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args, parser)
    except DeploymentError as exc:
        print(f"openmanus: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
