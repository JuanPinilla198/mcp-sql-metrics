"""Database access: read-only, parameterized, bounded."""

from __future__ import annotations

from typing import Any

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    """A small wrapper that only ever opens read-only transactions."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open()
        self._pool.wait(timeout=10)

    def close(self) -> None:
        self._pool.close()

    def run(
        self,
        statement: str,
        params: dict[str, Any],
        *,
        max_rows: int,
        statement_timeout_ms: int,
    ) -> list[dict[str, Any]]:
        """Execute a parameterized read and return at most `max_rows` rows.

        The connection is put in read-only mode for the duration of the
        transaction, so a write cannot happen even if one slipped past the
        load-time validation in `safety.py`.
        """
        with self._pool.connection() as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SET LOCAL statement_timeout = {}").format(
                        sql.Literal(statement_timeout_ms)
                    )
                )
                cur.execute(statement, params)
                return cur.fetchmany(max_rows)
