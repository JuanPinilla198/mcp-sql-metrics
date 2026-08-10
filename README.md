# mcp-sql-metrics

An [MCP](https://modelcontextprotocol.io) server that exposes read-only SQL queries as typed tools.

You declare your queries in a YAML file. The server turns each one into an MCP tool with a real argument schema, runs it as a parameterized read against PostgreSQL, and returns the rows as JSON.

## Why this exists

The intuitive way to let a language model answer questions about your data is to export the data and put it in the prompt. That approach breaks down for three reasons:

- **The context window doesn't fit a real history.** A year of orders is not going in a prompt.
- **Cost scales with the size of the dump**, not with the size of the answer.
- **Models make arithmetic errors.** Asking one to sum a column is asking for a wrong number that looks right.

The approach that works is the opposite: give the model *tools*, not data. The model decides which question to ask and interprets the result; PostgreSQL does the arithmetic, because PostgreSQL does not make arithmetic mistakes.

This server is that pattern, packaged. It also means the model never sees a full table — only the aggregated result of a query it asked for, which is a meaningful difference when the underlying data is sensitive.

## Install

```bash
pip install mcp-sql-metrics
```

## Use

Write a query catalogue:

```yaml
queries:
  - name: daily_sales
    description: >
      Returns total revenue, order count and units sold for a single day.
      Use it when the user asks how a specific date performed.
    params:
      - name: day
        type: string
        description: The date to report on, in YYYY-MM-DD format.
    sql: |
      SELECT
        COUNT(*)                AS orders,
        COALESCE(SUM(total), 0) AS revenue
      FROM orders
      WHERE DATE(created_at) = %(day)s
```

Run it:

```bash
export DATABASE_URL="postgresql://user:pass@host/db"
export QUERIES_FILE="queries.yaml"
mcp-sql-metrics
```

Or point an MCP client at it:

```json
{
  "mcpServers": {
    "sql-metrics": {
      "command": "mcp-sql-metrics",
      "env": {
        "DATABASE_URL": "postgresql://user:pass@host/db",
        "QUERIES_FILE": "/path/to/queries.yaml"
      }
    }
  }
}
```

To explore the tools interactively:

```bash
uv run mcp dev src/mcp_sql_metrics/server.py
```

## Safety model

Read-only is enforced in three independent places, because one check is a single point of failure:

1. **At load time**, every statement must start with `SELECT` or `WITH`, must be a single statement, and must not contain a write or DDL keyword. A bad query stops the server from starting rather than failing later.
2. **At execution time**, the connection is put in read-only mode for the transaction, so a write cannot happen even if a statement slipped past the first check.
3. **Arguments are always bound as query parameters**, never interpolated into the SQL string. There is no code path that builds a statement by concatenation.

Two more limits protect the caller rather than the database:

- **Row cap** per query (default 200). When a result is capped, the response says so explicitly, so the model knows to narrow its filters instead of assuming it saw everything.
- **Statement timeout** per query (default 10s), set with `SET LOCAL` so it cannot leak to another transaction.

## Error handling

Errors are returned to the model as structured JSON rather than raised, and they are written so the model can correct itself:

```json
{
  "error": "missing required argument 'day'",
  "expected": ["day"]
}
```

A model that gets `"expected": ["day"]` back will usually fix the call on its own. A model that gets a stack trace will not.

## Configuration reference

| Field | Required | Default | Notes |
|---|---|---|---|
| `name` | yes | — | Becomes the tool name; must be a valid identifier |
| `description` | yes | — | What the model reads to decide when to call the tool |
| `sql` | yes | — | A single `SELECT` or `WITH … SELECT` |
| `params` | no | `[]` | `name`, `type`, `description`; `default` makes it optional |
| `max_rows` | no | `200` | Rows returned before the result is marked truncated |
| `timeout_ms` | no | `10000` | Per-statement timeout |

Parameter types: `string`, `integer`, `number`, `boolean`. The set is deliberately small — every type the model can send maps to something the driver binds safely.

## Environment

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL DSN. The server exits at startup if unset. |
| `QUERIES_FILE` | no | Path to the catalogue. Defaults to `queries.yaml`. |

Point it at a database role with `SELECT`-only grants. The read-only transaction is a safety net, not a substitute for correct permissions.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
