# Automatizovani testovi

Skup testova proverava aktuelnu implementaciju iz direktorijuma `microservices/`. Jedinični testovi koriste determinističke ulaze i zamenske objekte; ne pozivaju aktivne OAI-PMH repozitorijume, Ollama servis, spoljne API-je, PostgreSQL, Docker ni Kubernetes.

## Lokalne zavisnosti

- Python i paketi za izvršavanje iz `requirements.txt`.
- Pytest iz `requirements-dev.txt`.
- Docker Desktop je potreban samo za PostgreSQL/pgvector integracione testove.
- Minikube, Kubernetes deployment, pokrenut aplikacioni sistem, Ollama model i pristupni podaci za spoljne API-je nisu potrebni.

Iz korena repozitorijuma treba instalirati postojeće zavisnosti za izvršavanje i zavisnosti namenjene testiranju:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Jedinični testovi

```powershell
.\.venv\Scripts\python.exe -m pytest -m unit
```

Ova komanda ne zahteva Docker, PostgreSQL, Kubernetes, pokrenut aplikacioni sistem, aktivan repozitorijum ni LLM servis.

## PostgreSQL/pgvector integracioni testovi

Potrebno je pokrenuti izolovanu bazu i usmeriti testove na nju:

```powershell
docker compose -f docker-compose.test.yml up -d --wait
$env:TEST_DATABASE_URL = "postgresql://repo_search_test:repo_search_test@127.0.0.1:55432/repo_search_test"
.\.venv\Scripts\python.exe -m pytest -m integration
docker compose -f docker-compose.test.yml down --volumes
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
```

Integracioni fixture-i prave šeme sa jedinstvenim nazivima i uklanjaju ih nakon svakog testa. Docker servis koristi privremeno skladište unutar kontejnera i ne zahteva pokretanje cele aplikacije. Testovi pouzdanosti poslova koriste PostgreSQL transakcije i zaključavanje, dok je za testove vektorske pretrage dodatno potrebna ekstenzija `vector`.

Kada `TEST_DATABASE_URL` nije postavljen ili baza nije dostupna, PostgreSQL integracioni testovi se pronalaze, ali se označavaju kao preskočeni. Ako je PostgreSQL dostupan bez ekstenzije `vector`, testovi pouzdanosti poslova mogu da se izvrše, dok se pgvector testovi preskaču. Greška tokom provere ponašanja na dostupnoj i podešenoj bazi smatra se neuspehom testa.

## Kompletan skup testova

Kada je izolovana baza pokrenuta i `TEST_DATABASE_URL` postavljen kao u prethodnom primeru:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Bez PostgreSQL/pgvector baze ista komanda pokreće sve jedinične testove, dok se integracioni testovi baze prikazuju kao preskočeni.
