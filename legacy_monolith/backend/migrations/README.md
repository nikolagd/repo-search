# Migracije baze podataka

Migracije koje još nisu primenjene pokreću se komandom:

```bash
python -m etl.migrate
```

Program koristi postojeće environment promenljive `DB_HOST`, `DB_PORT`, `DB_NAME`,
`DB_USER` i `DB_PASSWORD`.

Pri pokretanju kroz Docker, komandu treba izvršiti nakon što PostgreSQL počne da
prihvata konekcije, a pre pokretanja FastAPI ili ETL worker procesa.

U FastAPI kontejneru vrednost `RUN_DB_MIGRATIONS_ON_STARTUP=true` omogućava
primenu migracija prilikom pokretanja aplikacije.

PostgreSQL image mora da sadrži `pgvector`. Prva migracija izvršava
`CREATE EXTENSION IF NOT EXISTS vector`.
