# Repo Search

Repo Search je veb aplikacija za pretragu akademskih repozitorijuma. Frontend je urađen u React-u, backend u FastAPI-ju, a podaci se čuvaju u PostgreSQL bazi sa `pgvector` ekstenzijom.

Aplikacija podržava:

- javnu pretragu publikacija
- administrativni panel za repozitorijume i poslove osvežavanja podataka
- autentifikaciju korisnika
- tri korisničke role: `admin`, `editor`, `viewer`
- REST API rute koje vraćaju JSON odgovore

## Tehnologije

- React + TypeScript
- React Router
- FastAPI
- PostgreSQL + pgvector
- Docker Compose
- Ollama za LLM parsiranje upita

## Pokretanje preko Docker Compose-a

Napraviti lokalni env fajl:

```powershell
copy .env.docker.example .env.docker
```

U `.env.docker` promeniti bar ove vrednosti:

```env
API_TOKEN=replace_with_a_long_random_local_token
ADMIN_JWT_SECRET=replace_with_a_different_long_random_admin_jwt_secret
DB_PASSWORD=repo_search_password
```

Po potrebi promeniti i portove:

```env
FRONTEND_HOST_PORT=8080
API_HOST_PORT=8010
POSTGRES_HOST_PORT=5432
OLLAMA_HOST_PORT=11434
```

Ako je `FRONTEND_HOST_PORT=8080`, aplikacija je dostupna na:

```text
http://localhost:8080
```

Ako pokrećete izolovani stack sa drugim vrednostima, koristite port iz `.env.docker` ili iz `docker compose ps`. Na primer, ako je frontend mapiran kao `18080->80`, aplikacija je na:

```text
http://localhost:18080
```

Pokretanje celog stack-a:

```powershell
docker compose --env-file .env.docker up --build -d
```

Prvi put povući Ollama model:

```powershell
docker compose --env-file .env.docker exec ollama ollama pull llama3.1:8b
```

Status kontejnera:

```powershell
docker compose --env-file .env.docker ps
```

Zaustavljanje bez brisanja podataka:

```powershell
docker compose --env-file .env.docker down
```

Ne koristiti `down -v` osim ako namerno brišete Docker volume-e.

## Administracija i korisnici

Prvo otvaranje admin registracije:

```text
http://localhost:8080/admin/register
```

Registracija je dozvoljena samo dok u bazi ne postoji nijedan admin korisnik. Nakon toga se novi korisnici kreiraju iz admin panela.

Prijava:

```text
http://localhost:8080/admin/login
```

Role:

- `admin`: upravlja korisnicima, repozitorijumima, harvest poslovima i embedding backfill akcijama
- `editor`: upravlja repozitorijumima, harvest poslovima i embedding backfill akcijama, ali ne upravlja korisnicima
- `viewer`: ima read-only pristup zaštićenim admin podacima

## Frontend rute

- `/search`: javna pretraga publikacija
- `/about`: pregled funkcionalnosti i arhitekture aplikacije
- `/admin/login`: prijava administrativnog korisnika
- `/admin/register`: inicijalna registracija prvog admina
- `/admin`: administrativni panel

## API rute

Javne API rute:

- `GET /api/health`
- `GET /api/stats`
- `GET /api/repositories`
- `POST /api/search`

Auth rute:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/logout`

Admin rute:

- `GET /api/admin/repositories`
- `POST /api/admin/repositories`
- `PUT /api/admin/repositories/{repo_id}`
- `POST /api/admin/repositories/{repo_id}/harvest`
- `GET /api/admin/embeddings`
- `POST /api/admin/embeddings/backfill`
- `POST /api/admin/jobs/{job_id}/acknowledge`
- `GET /api/admin/users`
- `POST /api/admin/users`

CREATE/UPDATE/admin akcije zahtevaju autentifikaciju, CSRF token i odgovarajuću rolu.

## Baza i migracije

Migracije se nalaze u `migrations/`.

Trenutni model uključuje povezane entitete:

- `repository`
- `publication`
- `author`
- `publication_author`
- `admin_job`
- `admin_user`

Tabela `admin_user` ima kolonu:

```sql
role TEXT NOT NULL DEFAULT 'admin'
```

Dozvoljene vrednosti su ograničene CHECK constraint-om:

```sql
role IN ('admin', 'editor', 'viewer')
```

Ako je u `.env.docker` postavljeno:

```env
RUN_DB_MIGRATIONS_ON_STARTUP=true
```

migracije se pokreću automatski pri startovanju API kontejnera.

## Korisne komande

Logovi:

```powershell
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f frontend
docker compose --env-file .env.docker logs -f db
docker compose --env-file .env.docker logs -f ollama
```

Ulazak u kontejnere:

```powershell
docker compose --env-file .env.docker exec api bash
docker compose --env-file .env.docker exec db bash
docker compose --env-file .env.docker exec ollama bash
```

Pristup bazi:

```powershell
docker compose --env-file .env.docker exec db psql -U repo_search -d repo_search
```

Lista Ollama modela:

```powershell
docker compose --env-file .env.docker exec ollama ollama list
```

Provera GPU zauzeća:

```powershell
nvidia-smi
```

## Troubleshooting

Ako se frontend ne otvara, prvo proveriti stvarni mapirani port:

```powershell
docker compose --env-file .env.docker ps
```

Ako API nije dostupan:

```powershell
docker compose --env-file .env.docker logs --tail 100 api
```

Ako se frontend otvara, ali API pozivi ne rade:

```powershell
curl.exe http://localhost:8080/api/health
```

Ako koristite drugi frontend port, zamenite `8080` odgovarajućim portom.

Ako pretraga ne radi, proveriti da li je Ollama model prisutan:

```powershell
docker compose --env-file .env.docker exec ollama ollama list
```

Ako model nije prisutan:

```powershell
docker compose --env-file .env.docker exec ollama ollama pull llama3.1:8b
```
