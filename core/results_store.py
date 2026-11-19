"""Persists check results to a MySQL/MariaDB database for historical reporting.

Independent from db_client.DBClient: that one is a read-only connection to each
project's own database, this one is a writable connection to a separate results store.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime

import pymysql
import pymysql.cursors

from .checks import FAIL, OK, WARN, CheckResult


class ResultsStoreError(Exception):
    pass


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS inspection_runs (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        project VARCHAR(100) NOT NULL,
        environment VARCHAR(50) NOT NULL,
        host VARCHAR(255) NOT NULL,
        started_at DATETIME NOT NULL,
        finished_at DATETIME NOT NULL,
        overall_status VARCHAR(10) NOT NULL,
        ok_count INT NOT NULL,
        warn_count INT NOT NULL,
        fail_count INT NOT NULL,
        INDEX idx_project_env (project, environment, started_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS inspection_results (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        run_id BIGINT UNSIGNED NOT NULL,
        check_name VARCHAR(255) NOT NULL,
        check_type VARCHAR(100) NOT NULL,
        status VARCHAR(10) NOT NULL,
        summary TEXT,
        details LONGTEXT,
        FOREIGN KEY (run_id) REFERENCES inspection_runs(id) ON DELETE CASCADE,
        INDEX idx_run (run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


class ResultsStore:
    def __init__(self, host: str, user: str, password: str, name: str,
                 port: int = 3306, timeout: int = 10):
        self._params = dict(
            host=host, port=int(port), user=user, password=password, database=name,
            connect_timeout=timeout, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        )
        self._conn = None

    @classmethod
    def from_env(cls) -> "ResultsStore | None":
        """Builds a store from RESULTS_DB_* variables, or None if saving is not configured."""
        host = os.environ.get("RESULTS_DB_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("RESULTS_DB_PORT", 3306)),
            user=os.environ.get("RESULTS_DB_USER", ""),
            password=os.environ.get("RESULTS_DB_PASSWORD", ""),
            name=os.environ.get("RESULTS_DB_NAME", "sys_inspector"),
        )

    def connect(self) -> None:
        try:
            self._conn = pymysql.connect(**self._params)
            with self._conn.cursor() as cur:
                for stmt in _SCHEMA:
                    cur.execute(stmt)
        except pymysql.MySQLError as exc:
            raise ResultsStoreError(f"Results DB connection failed: {exc}") from exc

    def save(self, project: str, environment: str, results: list[CheckResult],
             started_at: datetime, finished_at: datetime) -> int:
        if not self._conn:
            raise ResultsStoreError("Results DB connection not open")
        counts = {s: sum(r.status == s for r in results) for s in (OK, WARN, FAIL)}
        overall = FAIL if counts[FAIL] else WARN if counts[WARN] else OK
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO inspection_runs "
                    "(project, environment, host, started_at, finished_at, overall_status, "
                    " ok_count, warn_count, fail_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (project, environment, socket.gethostname(), started_at, finished_at,
                     overall, counts[OK], counts[WARN], counts[FAIL]),
                )
                run_id = cur.lastrowid
                cur.executemany(
                    "INSERT INTO inspection_results "
                    "(run_id, check_name, check_type, status, summary, details) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    [(run_id, r.name, r.type, r.status, r.summary, "\n".join(r.details))
                     for r in results],
                )
            return run_id
        except pymysql.MySQLError as exc:
            raise ResultsStoreError(f"Could not save results: {exc}") from exc

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ResultsStore":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
