"""MCP server that exposes read-only SQL queries as typed tools.

The model never sees a table. It sees a set of named tools with typed
arguments; each one runs a parameterized SELECT and returns the rows. The
database does the arithmetic, which removes a whole class of calculation
errors and keeps the payload small enough to fit in a context window.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from mcp.server import MCPServer

from .db import Database
from .queries import Query, load

DEFAULT_CATALOGUE = Path("queries.yaml")


def _build_handler(query: Query, db: Database) -> Callable[..., str]:
    """Create a typed function for one query.

    The MCP SDK derives the tool schema from the function signature, so the
    signature is built at runtime from the query's declared parameters. That
    is what lets the catalogue be data instead of code.
    """

    def handler(**kwargs: Any) -> str:
        bound = {}
        for param in query.params:
            if param.name in kwargs and kwargs[param.name] is not None:
                bound[param.name] = kwargs[param.name]
            elif not param.required:
                bound[param.name] = param.default
            else:
                # Phrased so the model can correct itself on the next call.
                return json.dumps(
                    {
                        "error": f"missing required argument '{param.name}'",
                        "expected": [p.name for p in query.params],
                    }
                )

        try:
            rows = db.run(
                query.statement,
                bound,
                max_rows=query.max_rows,
                statement_timeout_ms=query.timeout_ms,
            )
        except Exception as exc:  # surfaced to the model, not swallowed
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "detail": str(exc).strip(),
                    "hint": "Check the argument types and try again.",
                }
            )

        payload: dict[str, Any] = {"row_count": len(rows), "rows": rows}
        if len(rows) == query.max_rows:
            payload["truncated"] = True
            payload["note"] = (
                f"Result was capped at {query.max_rows} rows. Narrow the "
                "filters if you need the full set."
            )

        return json.dumps(payload, default=str, ensure_ascii=False)

    signature_params = [
        inspect.Parameter(
            param.name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=param.python_type,
            default=(
                inspect.Parameter.empty if param.required else param.default
            ),
        )
        for param in query.params
    ]

    handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        signature_params, return_annotation=str
    )
    handler.__annotations__ = {
        param.name: param.python_type for param in query.params
    } | {"return": str}
    handler.__name__ = query.name

    argument_docs = "\n".join(
        f"    {p.name} ({p.type}): {p.description}" for p in query.params
    )
    handler.__doc__ = (
        f"{query.description}\n\nArgs:\n{argument_docs}"
        if argument_docs
        else query.description
    )

    return handler


def build_server(catalogue: Path, dsn: str) -> tuple[MCPServer, Database]:
    queries = load(catalogue)
    db = Database(dsn)

    mcp = MCPServer("sql-metrics")

    for query in queries:
        mcp.tool()(_build_handler(query, db))

    @mcp.resource("catalogue://queries")
    def catalogue_listing() -> str:
        """The queries this server exposes, with their arguments."""
        return json.dumps(
            [
                {
                    "name": q.name,
                    "description": q.description,
                    "params": [
                        {
                            "name": p.name,
                            "type": p.type,
                            "required": p.required,
                            "description": p.description,
                        }
                        for p in q.params
                    ],
                }
                for q in queries
            ],
            indent=2,
            ensure_ascii=False,
        )

    return mcp, db


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        # Fail at startup rather than on the first tool call.
        sys.exit("DATABASE_URL is not set")

    catalogue = Path(os.environ.get("QUERIES_FILE", DEFAULT_CATALOGUE))
    if not catalogue.exists():
        sys.exit(f"query catalogue not found: {catalogue}")

    mcp, db = build_server(catalogue, dsn)
    db.open()
    try:
        mcp.run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
