# Repo Search microservice pokretanje

Ovo uputstvo je za pokretanje aplikacije preko Docker Compose-a.

Za Kubernetes/Minikube pokretanje pogledati [k8s/README.md](k8s/README.md).

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

## 4. Ollama model

Model treba povući jednom nakon prvog pokretanja:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama pull llama3.1:8b
```

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

## 7. Osnovni troubleshooting

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
docker compose --env-file .env.microservices -f docker-compose.microservices.yml exec ollama ollama pull llama3.1:8b
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
