# Docker Compose microservices deployment

Ovo uputstvo je arhivirano zato što je Kubernetes sada primarni način deployment-a. Koristiti ga samo za lokalni smoke test, debugging ili poređenje sa Kubernetes manifestima.

## 1. Preduslovi

Potrebno je:

- Docker Desktop
- NVIDIA driver ako se koristi GPU
- Docker GPU podrška ako se koristi CUDA

Provera GPU-a:

```powershell
nvidia-smi
```

## 2. Podešavanje `.env` fajla

Kopirati primer konfiguracije:

```powershell
copy .env.microservices.example .env.microservices
```

U `.env.microservices` promeniti bar ove vrednosti:

```text
API_TOKEN
ADMIN_JWT_SECRET
DB_PASSWORD
DB_REPLICATION_PASSWORD
```

## 3. Pokretanje aplikacije

Pokrenuti sve kontejnere:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml up --build -d
```

Proveriti status:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml ps
```

Kreirati prvi administratorski nalog interaktivnom bootstrap komandom:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec auth-service python -m microservices.auth_service.bootstrap_admin
```

Lozinka se unosi preko `getpass` i ne prikazuje se u izlazu. Komanda odbija ponovno kreiranje ako administrator već postoji; trajne bootstrap lozinke se ne čuvaju u Compose konfiguraciji.

## 4. Ollama model

Model treba povući jednom nakon prvog pokretanja:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama pull gemma4:12b
```

Zagrejati model pre prve pretrage:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama run gemma4:12b "Return only: ok"
docker compose --env-file .env.microservices -f docker-compose.microservices.yml up -d query-service
```

`query-service` radi LLM warm-up na startup-u kada je `LLM_WARMUP_ENABLED=1`.

Proveriti modele:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama list
```

## 5. Otvaranje aplikacije

Frontend:

```text
http://localhost:8091
```

Gateway health check:

```powershell
curl.exe -H "X-API-Key: <API_TOKEN_IZ_ENV_FAJLA>" http://localhost:8090/api/health
```

Ollama API:

```text
http://localhost:11435
```

Observability:

```text
Grafana: http://localhost:3000
Prometheus: http://localhost:9090
cAdvisor: http://localhost:8088, optional container-metrics profile
```

Detalji su u [observability.md](observability.md).

## 6. Korisne komande

Logovi gateway servisa:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f gateway
```

Logovi harvest/background workera:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f job-worker
```

Logovi search servisa:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f search-service
```

Logovi embedding servisa:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f embedding-service
```

Provera da li Ollama trenutno koristi model:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama ps
```

Provera GPU zauzeća:

```powershell
nvidia-smi
```

## 7. Troubleshooting

Ako frontend ne radi, proveriti kontejnere:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml ps
```

Ako neki servis nije `running` ili `healthy`, proveriti njegove logove:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f <ime-servisa>
```

Ako login ili API pozivi ne rade, proveriti da li je `API_TOKEN` isti u `.env.microservices` i u zahtevu:

```powershell
curl.exe -H "X-API-Key: <API_TOKEN_IZ_ENV_FAJLA>" http://localhost:8090/api/health
```

Ako query/search ne radi zbog Ollama modela, proveriti i povući model:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama list
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama pull gemma4:12b
```

Ako embedding radi sporo, proveriti GPU:

```powershell
nvidia-smi
docker compose --env-file .env.microservices -f docker-compose.microservices.yml logs -f embedding-service
```

## 8. Zaustavljanje

Zaustaviti kontejnere bez brisanja podataka:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml down
```

Zaustaviti i obrisati volume podatke:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml down -v
```

`down -v` koristiti samo kada namerno želiš čisto pokretanje bez prethodnih podataka.
