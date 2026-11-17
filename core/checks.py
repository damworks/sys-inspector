"""Check types. To add one: subclass Check + @register."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from .db_client import DBClient, format_size
from .ssh_client import SSHClient

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class CheckResult:
    name: str
    type: str
    status: str
    summary: str = ""
    details: list[str] = field(default_factory=list)


class Check:
    type = ""
    needs = ""  # "ssh" | "db"

    def __init__(self, spec: dict):
        self.spec = spec
        self.name = spec.get("name") or self.type

    def run(self, ctx: "Context") -> CheckResult:
        raise NotImplementedError


@dataclass
class Context:
    env: dict
    ssh: SSHClient | None = None
    db: DBClient | None = None
    tunnel: object | None = None

    @property
    def workdir(self) -> str | None:
        return (self.env.get("ssh") or {}).get("workdir")


REGISTRY: dict[str, type[Check]] = {}


def register(cls: type[Check]) -> type[Check]:
    REGISTRY[cls.type] = cls
    return cls


@register
class SSHDiskUsage(Check):
    """du -h --max-depth=1 <path> | sort -hr for each path (relative to workdir)."""

    type = "ssh_disk_usage"
    needs = "ssh"

    def run(self, ctx: Context) -> CheckResult:
        paths = self.spec.get("paths") or []
        if not paths:
            return CheckResult(self.name, self.type, FAIL, "No 'paths' configured")
        limit = int(self.spec.get("top", 10))
        details, status = [], OK
        for p in paths:
            res = ctx.ssh.run(
                f"du -h --max-depth=1 {shlex.quote(p)} | sort -hr", ctx.workdir
            )
            details.append(f"[{p}]")
            lines = res.stdout.strip().splitlines()
            # pipefail is not available in sh: du errors show up on stderr
            if res.stderr.strip():
                status = WARN if lines else FAIL
                details += [f"  ! {l}" for l in res.stderr.strip().splitlines()[:5]]
            details += [f"  {l}" for l in lines[:limit]]
            if len(lines) > limit:
                details.append(f"  ... ({len(lines) - limit} more entries)")
        return CheckResult(self.name, self.type, status, f"{len(paths)} paths analyzed", details)


@register
class ConsoleCommand(Check):
    """Run commands in the workdir (e.g. php bin/console messenger:stats)."""

    type = "console_command"
    needs = "ssh"

    def run(self, ctx: Context) -> CheckResult:
        cmd = self.spec.get("command")
        if not cmd:
            return CheckResult(self.name, self.type, FAIL, "'command' is missing")
        # Re-quoting every token prevents shell metacharacter injection
        command = " ".join(shlex.quote(t) for t in shlex.split(cmd))
        res = ctx.ssh.run(command, ctx.workdir)
        expected = int(self.spec.get("expect_exit_code", 0))
        output = (res.stdout + res.stderr).strip()
        status = OK if res.exit_code == expected else FAIL
        needle = self.spec.get("fail_if_contains")
        if status == OK and needle and needle.lower() in output.lower():
            status = FAIL
        warn = self.spec.get("warn_if_contains")
        if status == OK and warn and warn.lower() in output.lower():
            status = WARN
        return CheckResult(
            self.name,
            self.type,
            status,
            f"exit code {res.exit_code}",
            [f"$ {cmd}"] + output.splitlines(),
        )


@register
class DBTableAnalysis(Check):
    """Existence, row count and size of tables matching a LIKE pattern."""

    type = "db_table_analysis"
    needs = "db"

    def run(self, ctx: Context) -> CheckResult:
        pattern = self.spec.get("pattern")
        if not pattern:
            return CheckResult(self.name, self.type, FAIL, "'pattern' is missing")
        tables = ctx.db.analyze_tables(pattern, bool(self.spec.get("exact_count", False)))
        if not tables:
            status = FAIL if self.spec.get("must_exist", True) else WARN
            return CheckResult(self.name, self.type, status, f"No tables matching '{pattern}'")

        total = sum(t.total_mb for t in tables)
        status = OK
        warn_mb = self.spec.get("warn_total_mb")
        fail_mb = self.spec.get("fail_total_mb")
        if warn_mb is not None and total >= float(warn_mb):
            status = WARN
        if fail_mb is not None and total >= float(fail_mb):
            status = FAIL

        details = []
        for t in tables:
            approx = "" if t.rows_exact else "~"
            details.append(
                f"{t.name:<40} rows: {approx}{t.rows:>12,}  "
                f"data: {format_size(t.data_mb):>10}  idx: {format_size(t.index_mb):>10}  "
                f"total: {format_size(t.total_mb):>10}"
            )
        return CheckResult(
            self.name,
            self.type,
            status,
            f"{len(tables)} tables, total {format_size(total)}",
            details,
        )
