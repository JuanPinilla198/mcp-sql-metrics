"""Load and validate the declarative query catalogue."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from .safety import assert_read_only

# Only these types are exposed to the model. Keeping the set small means every
# argument the model can send maps to something the driver can bind safely.
PYTHON_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


@dataclasses.dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str
    default: Any = dataclasses.field(default=None)
    required: bool = True

    @property
    def python_type(self) -> type:
        return PYTHON_TYPES[self.type]


@dataclasses.dataclass(frozen=True)
class Query:
    name: str
    description: str
    statement: str
    params: tuple[Param, ...]
    max_rows: int = 200
    timeout_ms: int = 10_000


def _parse_param(query_name: str, raw: dict[str, Any]) -> Param:
    for field in ("name", "type", "description"):
        if field not in raw:
            raise ValueError(
                f"query '{query_name}': parameter is missing '{field}'"
            )

    if raw["type"] not in PYTHON_TYPES:
        raise ValueError(
            f"query '{query_name}': parameter '{raw['name']}' has unsupported "
            f"type '{raw['type']}'. Supported: {', '.join(PYTHON_TYPES)}"
        )

    has_default = "default" in raw
    return Param(
        name=raw["name"],
        type=raw["type"],
        description=raw["description"],
        default=raw.get("default"),
        required=not has_default,
    )


def load(path: Path) -> list[Query]:
    """Read the YAML catalogue, validating every entry before returning."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_queries = data.get("queries")

    if not raw_queries:
        raise ValueError(f"{path}: no 'queries' defined")

    queries: list[Query] = []
    seen: set[str] = set()

    for raw in raw_queries:
        name = raw.get("name")
        if not name:
            raise ValueError(f"{path}: a query is missing 'name'")
        if not name.isidentifier():
            raise ValueError(
                f"query '{name}': name must be a valid Python identifier"
            )
        if name in seen:
            raise ValueError(f"duplicate query name '{name}'")
        seen.add(name)

        if not raw.get("description"):
            raise ValueError(
                f"query '{name}': 'description' is required — it is what the "
                "model reads to decide when to call this tool"
            )
        if not raw.get("sql"):
            raise ValueError(f"query '{name}': 'sql' is required")

        assert_read_only(name, raw["sql"])

        queries.append(
            Query(
                name=name,
                description=raw["description"].strip(),
                statement=raw["sql"].strip(),
                params=tuple(
                    _parse_param(name, p) for p in raw.get("params", [])
                ),
                max_rows=int(raw.get("max_rows", 200)),
                timeout_ms=int(raw.get("timeout_ms", 10_000)),
            )
        )

    return queries
