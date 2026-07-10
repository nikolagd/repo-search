# Automated tests

The test suite targets the current implementation under `microservices/`. Unit tests use deterministic inputs and mocks; they do not call live OAI-PMH repositories, Ollama, external APIs, PostgreSQL, Docker, or Kubernetes.

## Local dependencies

- Python and the runtime packages from `requirements.txt`.
- Pytest from `requirements-dev.txt`.
- Docker Desktop only for the pgvector integration test.
- No Minikube, Kubernetes deployment, running application stack, Ollama model, or external API credentials.

From the repository root, install the existing runtime dependencies and the test-only dependency:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Unit tests

```powershell
.\.venv\Scripts\python.exe -m pytest -m unit
```

This command does not require Docker, PostgreSQL, Kubernetes, the application stack, a live repository, or an LLM service.

## PostgreSQL/pgvector integration tests

Start the isolated database and point the test suite at it:

```powershell
docker compose -f docker-compose.test.yml up -d --wait
$env:TEST_DATABASE_URL = "postgresql://repo_search_test:repo_search_test@127.0.0.1:55432/repo_search_test"
.\.venv\Scripts\python.exe -m pytest -m integration
docker compose -f docker-compose.test.yml down --volumes
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
```

The integration fixture creates a uniquely named schema and removes it after the test. The Docker service uses container-local temporary storage and does not require the complete application deployment.

When `TEST_DATABASE_URL` is not set, cannot be reached, or does not expose the `vector` extension, pgvector integration tests are collected and reported as skipped. A configured database that is reachable but fails during the tested behavior causes a test failure.

## Complete suite

With the isolated database running and `TEST_DATABASE_URL` set as above:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Without PostgreSQL/pgvector, the same command runs all unit tests and reports the pgvector integration tests as skipped.
