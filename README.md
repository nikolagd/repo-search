# Repo Search Microservice Stack

Ova grana sadrži microservice/SOA verziju aplikacije. Aktivni Docker cluster je definisan u `docker-compose.microservices.yml`; stari monolitni backend je pomeren u `legacy_monolith/backend` samo kao referenca.

## Šta je potrebno

- Docker Desktop
- NVIDIA driver
- Docker GPU podrška, ako se koristi CUDA

## Podešavanje

Kopirati env fajl:

```powershell
copy .env.microservices.example .env.microservices
```

U `.env.microservices` promeniti bar ove vrednosti:

- `API_TOKEN`
- `ADMIN_JWT_SECRET`
- lozinke za `AUTH_DB_PASSWORD`, `CATALOG_DB_PASSWORD`, `JOB_DB_PASSWORD`, `SEARCH_DB_PASSWORD`

## Pokretanje

Pokrenuti microservice cluster:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml up --build -d
```

Ollama model treba povući jednom u novi cluster:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama pull llama3.1:8b
```

Adrese:

- Frontend: `http://localhost:8091`
- API gateway health: `http://localhost:8090/api/health`
- Ollama API za ovaj cluster: `http://localhost:11435`

## Servisi

Backend je razbijen na sledeće servise:

- `gateway`: API gateway kompatibilan sa postojećim frontend rutama pod `/api`
- `auth-service`: admin login, JWT cookie, rotacija sesije i CSRF validacija
- `catalog-service`: repozitorijumi, publikacije, autori i katalog statistika
- `search-service`: pgvector search read model i rangiranje rezultata
- `query-service`: parsiranje prirodnog jezika preko Ollama/LLM servisa
- `embedding-service`: SentenceTransformer embedding model
- `job-service`: stanje harvest/backfill poslova
- `job-worker`: background worker za harvest i embedding backfill

Stack koristi odvojene baze po servisima:

- `auth-db`
- `catalog-db`
- `job-db`
- `search-db`

`gateway`, `query-service`, `embedding-service` i `job-worker` nemaju sopstvenu aplikacionu bazu zato što ne poseduju trajne poslovne podatke. `embedding-service` koristi samo model cache volume.

## Korisne komande

Status kontejnera:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml ps
```

Logovi:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f gateway
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f catalog-service
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f search-service
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f job-worker
```

Lista Ollama modela:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama list
```

Provera da li Ollama model koristi GPU:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama ps
```

Provera GPU zauzeća:

```powershell
nvidia-smi
```

## Zaustavljanje

Zaustaviti microservice cluster, bez brisanja podataka:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml down
```

`down -v` koristiti samo ako namerno brišeš microservice volume podatke.

## Legacy monolit

Stari monolitni backend je arhiviran u:

```text
legacy_monolith/backend
```

Tu se nalaze raniji `api`, `etl`, `search`, `embeddings`, `migrations`, `Dockerfile.api` i `test_search.py`. Oni nisu kopirani u microservice Docker image i microservice kod ih ne importuje.

Stari monolitni Compose fajl je pomeren u:

```text
legacy_monolith/docker-compose.monolith.yml
```

Na ovoj grani taj fajl služi samo kao referenca. Aktivno pokretanje ide preko `docker-compose.microservices.yml`.
