"""Orchestration engine: config + .env -> connections -> checks -> results."""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from .checks import FAIL, REGISTRY, Check, CheckResult, Context
from .config import ConfigError, load_project_config, resolve_checks, resolve_environment
from .db_client import DBClient, DBError
from .ssh_client import SSHClient, SSHError
from .ssh_tunnel import SSHTunnel, TunnelError

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


def _as_bool(v, default=True) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class Engine:
    def __init__(self, project: str, env_name: str, config_dir: Path | None = None,
                 env_file: Path | None = None, only: list[str] | None = None):
        # Variables already present in the environment (Docker, CI) take precedence over .env
        load_dotenv(env_file or ROOT / ".env", override=False)
        self.project, self.env_name = project, env_name
        self.config = load_project_config(config_dir or ROOT / "config", project)
        self.env = resolve_environment(self.config, env_name)
        specs = resolve_checks(self.config, self.env)
        if only:
            specs = [s for s in specs if s.get("type") in only or s.get("name") in only]
        self.checks = [self._build(s) for s in specs]

    @staticmethod
    def _build(spec: dict) -> Check:
        t = spec.get("type")
        if t not in REGISTRY:
            raise ConfigError(f"Unknown check type: {t!r} (valid: {', '.join(REGISTRY)})")
        return REGISTRY[t](spec)

    def _open_ssh(self) -> SSHClient:
        cfg = self.env.get("ssh")
        if not cfg:
            raise SSHError("'ssh' section not configured for this environment")
        client = SSHClient(
            host=cfg["host"], user=cfg["user"], port=cfg.get("port", 22),
            key_path=cfg.get("key_path"), passphrase=cfg.get("passphrase"),
            known_hosts=cfg.get("known_hosts"),
            strict_host_key=_as_bool(cfg.get("strict_host_key")),
        )
        client.connect()
        return client

    def _open_db(self, ctx: Context) -> DBClient:
        cfg = self.env.get("database")
        if not cfg:
            raise DBError("'database' section not configured for this environment")
        host, port = cfg["host"], int(cfg.get("port", 3306))
        if _as_bool(cfg.get("tunnel"), default=False):
            # host/port are as seen from the SSH server; its connection is reused
            if ctx.ssh is None:
                ctx.ssh = self._open_ssh()
            try:
                ctx.tunnel = SSHTunnel(ctx.ssh.get_transport(), host, port).start()
            except (TunnelError, OSError) as exc:
                raise DBError(f"SSH tunnel failed: {exc}") from exc
            host, port = "127.0.0.1", ctx.tunnel.local_port
        client = DBClient(
            host=host, port=port, user=cfg["user"],
            password=cfg.get("password", ""), name=cfg["name"], ssl_ca=cfg.get("ssl_ca"),
        )
        client.connect()
        return client

    def run(self) -> list[CheckResult]:
        ctx = Context(env=self.env)
        results: list[CheckResult] = []
        # Connections are opened lazily, once, and only if needed
        openers = {"ssh": ("ssh", self._open_ssh), "db": ("db", lambda: self._open_db(ctx))}
        failed: dict[str, str] = {}
        try:
            for check in self.checks:
                kind = check.needs
                if kind in failed:
                    results.append(CheckResult(check.name, check.type, FAIL, failed[kind]))
                    continue
                if kind and getattr(ctx, kind) is None:
                    attr, opener = openers[kind]
                    try:
                        setattr(ctx, attr, opener())
                    except (SSHError, DBError, KeyError) as exc:
                        if kind == "db" and ctx.tunnel:
                            ctx.tunnel.close()
                            ctx.tunnel = None
                        log.warning("Connection '%s' unavailable: %s", kind, exc)
                        failed[kind] = f"Connection not available: {exc}"
                        results.append(CheckResult(check.name, check.type, FAIL, failed[kind]))
                        continue
                try:
                    log.debug("Running check '%s' (%s)", check.name, check.type)
                    results.append(check.run(ctx))
                except (SSHError, DBError) as exc:
                    log.warning("Check '%s' failed: %s", check.name, exc)
                    results.append(CheckResult(check.name, check.type, FAIL, str(exc)))
                except Exception as exc:  # a buggy check must not abort the whole run
                    log.exception("Unexpected error in check '%s'", check.name)
                    results.append(
                        CheckResult(
                            check.name, check.type, FAIL,
                            f"Unexpected error: {type(exc).__name__}: {exc}",
                        )
                    )
        finally:
            for c in (ctx.db, ctx.tunnel, ctx.ssh):
                if c:
                    c.close()
        return results
