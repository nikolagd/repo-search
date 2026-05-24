# Database migrations

Run pending migrations with:

```bash
python -m etl.migrate
```

The runner uses the existing `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and
`DB_PASSWORD` environment variables.

For Docker startup, run this command after Postgres is accepting connections and
before starting FastAPI or ETL workers.

For the FastAPI container, `RUN_DB_MIGRATIONS_ON_STARTUP=true` can be used to
run pending migrations during application startup.

The Postgres image must include `pgvector`; the first migration runs
`CREATE EXTENSION IF NOT EXISTS vector`.
