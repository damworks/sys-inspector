"""Paramiko wrapper with host key verification and key-based authentication."""
from __future__ import annotations

import logging
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko


log = logging.getLogger(__name__)


class SSHError(Exception):
    pass


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


class SSHClient:
    def __init__(
        self,
        host: str,
        user: str,
        key_path: str | None = None,
        passphrase: str | None = None,
        port: int = 22,
        known_hosts: str | None = None,
        strict_host_key: bool = True,
        timeout: int = 15,
        command_timeout: int = 120,
    ):
        self.host, self.user, self.port = host, user, int(port)
        self.key_path = os.path.expanduser(key_path) if key_path else None
        self.passphrase = passphrase or None
        self.known_hosts = os.path.expanduser(known_hosts) if known_hosts else None
        self.strict_host_key = strict_host_key
        self.timeout = timeout
        self.command_timeout = command_timeout
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if self.known_hosts:
            try:
                if not Path(self.known_hosts).is_file():
                    raise SSHError(f"known_hosts not found: {self.known_hosts}")
                client.load_host_keys(self.known_hosts)
            except OSError as exc:
                raise SSHError(f"known_hosts not readable: {exc}") from exc
        client.set_missing_host_key_policy(
            paramiko.RejectPolicy() if self.strict_host_key else paramiko.WarningPolicy()
        )
        if self.key_path and not Path(self.key_path).is_file():
            raise SSHError(f"SSH key not found: {self.key_path}")
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                key_filename=self.key_path,
                passphrase=self.passphrase,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=self.key_path is None,
                allow_agent=self.key_path is None,
            )
        except paramiko.SSHException as exc:
            raise SSHError(f"SSH connection to {self.host} failed: {exc}") from exc
        except OSError as exc:
            raise SSHError(f"SSH connection to {self.host} failed: {exc}") from exc
        self._client = client

    def run(
        self, command: str, workdir: str | None = None, timeout: int | None = None
    ) -> CommandResult:
        """Run a command; `workdir` is safely quoted. `timeout` overrides command_timeout."""
        if not self._client:
            raise SSHError("SSH connection not open")
        full = f"cd {shlex.quote(workdir)} && {command}" if workdir else command
        limit = timeout if timeout is not None else self.command_timeout
        log.debug("SSH exec (timeout %ss): %s", limit, full)
        start = time.monotonic()
        try:
            _, stdout, stderr = self._client.exec_command(full, timeout=limit)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            log.debug("SSH exit code %s in %.1fs", code, time.monotonic() - start)
        except TimeoutError as exc:
            log.warning("SSH command timed out after %ss: %s", limit, full)
            raise SSHError(f"Command timed out after {limit}s: {command}") from exc
        except (paramiko.SSHException, OSError) as exc:
            raise SSHError(f"Command execution failed: {exc}") from exc
        return CommandResult(full, code, out, err)

    def get_transport(self) -> paramiko.Transport | None:
        return self._client.get_transport() if self._client else None

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
