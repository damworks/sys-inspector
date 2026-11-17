"""Loads project YAML files with environment variable interpolation."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

# ${VAR}  or  ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class ConfigError(Exception):
    pass


def _interpolate(value: Any, missing: set[str]) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            env = os.environ.get(name)
            if env is not None and env != "":
                return env
            if default is not None:
                return default
            missing.add(name)
            return ""

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, missing) for v in value]
    return value


def load_project_config(config_dir: Path, project: str) -> dict:
    if not _PROJECT_NAME.match(project):
        raise ConfigError(f"Invalid project name: {project!r}")
    path = Path(config_dir) / "projects" / f"{project}.yaml"
    if not path.is_file():
        raise ConfigError(f"Configuration not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or not isinstance(raw.get("environments"), dict):
        raise ConfigError(f"{path}: missing 'environments' section")
    return raw


def resolve_environment(config: dict, env_name: str) -> dict:
    """Return the requested environment with environment variables resolved."""
    envs = config["environments"]
    if env_name not in envs:
        raise ConfigError(
            f"Environment '{env_name}' is not defined. Available: {', '.join(envs)}"
        )
    missing: set[str] = set()
    env = _interpolate(envs[env_name] or {}, missing)
    if missing:
        raise ConfigError(
            "Missing environment variables: " + ", ".join(sorted(missing))
        )
    return env


def resolve_checks(config: dict, env: dict) -> list[dict]:
    """Project-level checks plus any environment-specific ones."""
    checks = list(config.get("checks") or []) + list(env.get("checks") or [])
    missing: set[str] = set()
    checks = _interpolate(checks, missing)
    if missing:
        raise ConfigError(
            "Missing environment variables in checks: " + ", ".join(sorted(missing))
        )
    return checks
