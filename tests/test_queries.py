"""The safety rules are the part that must not regress, so they are what we test."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mcp_sql_metrics.queries import load
from mcp_sql_metrics.safety import UnsafeQueryError, assert_read_only


def write_catalogue(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "queries.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# --- read-only enforcement -------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1",
        "select * from orders where id = %(id)s",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "  SELECT 1  ",
        "SELECT 1;",
    ],
)
def test_accepts_plain_reads(statement: str) -> None:
    assert_read_only("q", statement)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM orders",
        "UPDATE orders SET total = 0",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "ALTER TABLE orders ADD COLUMN x int",
        "GRANT ALL ON orders TO public",
        "COPY orders TO '/tmp/out.csv'",
    ],
)
def test_rejects_writes_and_ddl(statement: str) -> None:
    with pytest.raises(UnsafeQueryError):
        assert_read_only("q", statement)


def test_rejects_stacked_statements() -> None:
    with pytest.raises(UnsafeQueryError, match="multiple statements"):
        assert_read_only("q", "SELECT 1; DROP TABLE orders")


def test_rejects_write_hidden_after_a_select() -> None:
    # A single statement that starts with SELECT but smuggles in a write.
    with pytest.raises(UnsafeQueryError):
        assert_read_only("q", "SELECT * FROM orders WHERE 1=1 UNION DELETE")


# --- catalogue loading -----------------------------------------------------


def test_loads_a_valid_catalogue(tmp_path: Path) -> None:
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: daily_sales
            description: Revenue for one day.
            params:
              - name: day
                type: string
                description: Date in YYYY-MM-DD.
            sql: SELECT 1 WHERE %(day)s IS NOT NULL
        """,
    )

    (query,) = load(path)

    assert query.name == "daily_sales"
    assert query.max_rows == 200
    assert query.timeout_ms == 10_000
    assert query.params[0].required is True
    assert query.params[0].python_type is str


def test_a_default_makes_a_parameter_optional(tmp_path: Path) -> None:
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: recent
            description: Recent rows.
            params:
              - name: days
                type: integer
                description: Days back.
                default: 30
            sql: SELECT %(days)s
        """,
    )

    (query,) = load(path)
    param = query.params[0]

    assert param.required is False
    assert param.default == 30
    assert param.python_type is int


def test_rejects_a_missing_description(tmp_path: Path) -> None:
    # The description is what the model uses to pick the tool, so an empty
    # one is a defect rather than a style problem.
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: nameless
            sql: SELECT 1
        """,
    )

    with pytest.raises(ValueError, match="description"):
        load(path)


def test_rejects_a_name_that_is_not_an_identifier(tmp_path: Path) -> None:
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: daily-sales
            description: Revenue for one day.
            sql: SELECT 1
        """,
    )

    with pytest.raises(ValueError, match="identifier"):
        load(path)


def test_rejects_duplicate_names(tmp_path: Path) -> None:
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: dup
            description: First.
            sql: SELECT 1
          - name: dup
            description: Second.
            sql: SELECT 2
        """,
    )

    with pytest.raises(ValueError, match="duplicate"):
        load(path)


def test_rejects_an_unsupported_parameter_type(tmp_path: Path) -> None:
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: q
            description: Something.
            params:
              - name: when
                type: datetime
                description: A timestamp.
            sql: SELECT %(when)s
        """,
    )

    with pytest.raises(ValueError, match="unsupported"):
        load(path)


def test_a_write_in_the_catalogue_fails_at_load_time(tmp_path: Path) -> None:
    # This is the point of validating on load: the mistake surfaces when the
    # server starts, not when a model happens to call the tool.
    path = write_catalogue(
        tmp_path,
        """
        queries:
          - name: oops
            description: Looks harmless.
            sql: DELETE FROM orders
        """,
    )

    with pytest.raises(UnsafeQueryError):
        load(path)
