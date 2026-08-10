"""Statement validation. Deliberately free of database dependencies so the
rules can be tested without a running PostgreSQL."""

from __future__ import annotations

import re

# Statements that must never reach the database, even if someone puts them in
# the query file by mistake. The read-only transaction in `db.py` is the real
# guarantee; this is a fail-fast check so the mistake surfaces when the server
# starts instead of when a model happens to call the tool.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """Raised when a configured query is not a plain read."""


def assert_read_only(name: str, statement: str) -> None:
    """Reject anything that is not a single SELECT or WITH ... SELECT."""
    stripped = statement.strip().rstrip(";")

    if ";" in stripped:
        raise UnsafeQueryError(
            f"query '{name}': multiple statements are not allowed"
        )

    if not re.match(r"^\s*(select|with)\b", stripped, re.IGNORECASE):
        raise UnsafeQueryError(f"query '{name}': must start with SELECT or WITH")

    if _FORBIDDEN.search(stripped):
        raise UnsafeQueryError(
            f"query '{name}': contains a write or DDL keyword"
        )
