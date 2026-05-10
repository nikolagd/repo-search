# Harvest i pretraga repozitorijuma

Aplikacija se pokreće preko Docker Compose-a. Compose startuje:

- PostgreSQL sa `pgvector` ekstenzijom
- FastAPI backend
- React frontend kroz nginx
- Ollama za LLM parsiranje upita

PostgreSQL podaci se čuvaju u `./data/postgres`. Ollama modeli se čuvaju u Docker volume-u `repo-search_ollama_data`. `docker compose down` ne briše ni bazu ni Ollama modele.

## Šta je potrebno

- Docker Desktop
- NVIDIA driver
- Docker GPU podrška, ako se koristi CUDA

## Podešavanje

Napraviti env fajl:

```powershell
copy .env.docker.example .env.docker
```

U `.env.docker` promeniti bar ove vrednosti:

- `API_TOKEN`
- `ADMIN_JWT_SECRET`
- `DB_PASSWORD`
- `OAI_BASE_URL`, ako se koristi drugi OAI endpoint

Podrazumevani Ollama model je:

```env
LLM_MODEL=llama3.1:8b
```

GPU podešavanja su već uključena:

```env
NVIDIA_VISIBLE_DEVICES=all
NVIDIA_DRIVER_CAPABILITIES=compute,utility
OLLAMA_KEEP_ALIVE=30m
```

## Pokretanje

Pokrenuti ceo stack:

```powershell
docker compose --env-file .env.docker up --build -d
```

Prvi put povući Ollama model:

```powershell
docker compose --env-file .env.docker exec ollama ollama pull llama3.1:8b
```

Model ostaje sačuvan u Docker volume-u, pa se ne skida ponovo posle restartovanja kontejnera.

Aplikacija je dostupna na:

- `http://localhost:8080`
- API provera: `http://localhost:8080/api/health`
- Ollama API: `http://localhost:11434`

## Zaustavljanje

Zaustaviti kontejnere, bez brisanja podataka:

```powershell
docker compose --env-file .env.docker down
```

Ponovno pokretanje:

```powershell
docker compose --env-file .env.docker up -d
```

Ne koristiti `down -v` osim ako namerno treba obrisati Docker volume-e. Time se briše i Ollama model.

## Korisne komande

Status kontejnera:

```powershell
docker compose --env-file .env.docker ps
```

Logovi:

```powershell
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f frontend
docker compose --env-file .env.docker logs -f db
docker compose --env-file .env.docker logs -f ollama
```

Ulazak u kontejner:

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

Provera da li Ollama model koristi GPU:

```powershell
docker compose --env-file .env.docker exec ollama ollama ps
```

Provera GPU zauzeća:

```powershell
nvidia-smi
```

## Troubleshooting

Ako API nije zdrav:

```powershell
docker compose --env-file .env.docker logs --tail 100 api
```

Ako se frontend otvara, ali API pozivi ne rade:

```powershell
curl.exe http://localhost:8080/api/health
```

Ako pretraga ne radi, proveriti da li je Ollama model prisutan:

```powershell
docker compose --env-file .env.docker exec ollama ollama list
```

Ako model nije prisutan:

```powershell
docker compose --env-file .env.docker exec ollama ollama pull llama3.1:8b
```

Ako izgleda kao da Ollama koristi RAM umesto VRAM-a:

```powershell
docker compose --env-file .env.docker exec ollama ollama ps
nvidia-smi
```

`ollama ps` treba da prikaže `100% GPU`. Na Windows-u Task Manager ne prikazuje uvek jasno Docker/WSL VRAM zauzeće, pa su `ollama ps` i `nvidia-smi` pouzdaniji.

Ako embedding model ne koristi CUDA:

```powershell
docker compose --env-file .env.docker exec api python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')"
```

Ako podaci iz baze nestanu, proveriti da postoji `./data/postgres` i da stack nije zaustavljen sa:

```powershell
docker compose --env-file .env.docker down -v
```
