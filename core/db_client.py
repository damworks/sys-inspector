"""PyMySQL wrapper for analysis queries on MySQL/MariaDB (read-only)."""
from __future__ import annotations

from dataclasses import dataclass

import pymysql
import pymysql.cursors


class DBError(Exception):
    pass


@dataclass
class TableInfo:
    name: str
    rows: int
    rows_exact: bool
    data_mb: float
    index_mb: float

    @property
    def total_mb(self) -> float:
        return self.data_mb + self.index_mb


def format_size(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.2f} MB"


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


class DBClient:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        name: str,
        port: int = 3306,
        timeout: int = 15,
        ssl_ca: str | None = None,
    ):
        self.database = name
        self._params = dict(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=name,
            connect_timeout=timeout,
            read_timeout=timeout * 8,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        if ssl_ca:
            self._params["ssl"] = {"ca": ssl_ca}
        self._conn = None

    def connect(self) -> None:
        try:
            self._conn = pymysql.connect(**self._params)
            with self._conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        except pymysql.MySQLError as exc:
            raise DBError(f"Database connection failed: {exc}") from exc

    def analyze_tables(self, pattern: str, exact_count: bool = False) -> list[TableInfo]:
        """Tables matching the LIKE pattern (e.g. 'application_logs%').

        By default uses information_schema (estimated rows for InnoDB, fast);
        with exact_count=True runs SELECT COUNT(*) on each table.
        """
        if not self._conn:
            raise DBError("Database connection not open")
        sql = (
            "SELECT TABLE_NAME AS name, COALESCE(TABLE_ROWS, 0) AS table_rows, "
            "COALESCE(DATA_LENGTH, 0) AS data_length, "
            "COALESCE(INDEX_LENGTH, 0) AS index_length "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
            "AND TABLE_NAME LIKE %s ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC"
        )
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, (self.database, pattern))
                found = cur.fetchall()
                tables = []
                for r in found:
                    rows = int(r["table_rows"])
                    if exact_count:
                        cur.execute(f"SELECT COUNT(*) AS c FROM {_quote_ident(r['name'])}")
                        rows = int(cur.fetchone()["c"])
                    tables.append(
                        TableInfo(
                            name=r["name"],
                            rows=rows,
                            rows_exact=exact_count,
                            data_mb=int(r["data_length"]) / 1024 / 1024,
                            index_mb=int(r["index_length"]) / 1024 / 1024,
                        )
                    )
                return tables
        except pymysql.MySQLError as exc:
            raise DBError(f"Analysis query failed: {exc}") from exc

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "DBClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
