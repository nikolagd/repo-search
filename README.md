## Harvest repozitorijuma



### Podešavanja

Kloniranje

```bash
git clone https://github.com/nikolagd/repo-search.git
cd repo-search
```

Kreairanje i aktivacija virtuelnog okruženja (venv):
```bash
python -m venv .venv
.venv\Scripts\activate
```
Instaliranje dependencija:
```bash
pip install -r requirements.txt
```
Konfigurisanje .env fajla:

Kreirati .env fajl na osnovu .example.env. 
U njemu definisati konekciju ka bazi i oai endpoint.

`API_TOKEN` mora biti ista vrednost za FastAPI i Vite dev proxy. React kod ne
dobija token direktno; Vite ga cita sa serverske strane iz `.env` fajla i dodaje
`X-API-Key` samo kada proxy prosledjuje `/api` zahteve ka FastAPI-ju.

Za admin login potrebno je dodati i `ADMIN_JWT_SECRET`. To je odvojena tajna
od najmanje 32 bajta koja se koristi za potpisivanje JWT tokena posle login-a.
JWT se cuva u
`HttpOnly` cookie-ju, a admin `POST` rute koriste dodatni CSRF cookie/header
par (`repo_search_admin_csrf` i `X-CSRF-Token`) za zastitu cookie sesije.
Admin JWT ima rolling expiry: `ADMIN_JWT_EXPIRY_MINUTES` je trajanje novog
tokena, a aktivna sesija se osvezava kada je token stariji od
`ADMIN_JWT_ROTATION_INTERVAL_MINUTES`. `ADMIN_JWT_MAX_SESSION_MINUTES` je
gornja granica ukupnog trajanja sesije, cak i ako je korisnik aktivan.
Admin harvest i embedding poslovi se cuvaju u PostgreSQL tabeli `admin_job`.
`ADMIN_JOB_STATUS_HISTORY_MINUTES` odredjuje koliko dugo se zavrseni/failed
statusi vracaju admin UI-ju nakon sto posao vise ne radi. Zavrseni statusi se
mogu dismiss-ovati iz admin UI-ja; tada dobijaju `acknowledged_at` i vise se ne
prikazuju.


Pokretanje iz komandne linije sa:
```bash
python -m etl.main
```

`etl.main` koristi `OAI_BASE_URL` iz `.env` fajla da pronadje red u tabeli
`repository`. Za eksplicitno biranje repozitorijuma mogu se koristiti:

```bash
python -m etl.main --repo-url https://example.com/oai/request
python -m etl.main --repo-id 3
```

Ako URL jos ne postoji u tabeli `repository`, eksplicitno dodati:

```bash
python -m etl.main --repo-url https://example.com/oai/request --create-repo
```

Grube provere da li je upisivanje u bazu uspelo:
```bash
psql -U postgres -d [naziv_baze] -p [port] -f etl/checks.sql
```

### Migracije baze

Pre pokretanja API-ja, ETL-a ili Docker kontejnera koji zavise od baze,
primeniti migracije:

```bash
python -m etl.migrate
```

Komanda koristi `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` i `DB_PASSWORD` iz
okruzenja ili `.env` fajla. Za Docker entrypoint, pokrenuti ovu komandu nakon
sto je Postgres spreman za konekcije, a pre startovanja FastAPI servera.
Alternativno, za API kontejner se moze postaviti
`RUN_DB_MIGRATIONS_ON_STARTUP=true`, pa ce FastAPI primeniti pending migracije
pri startovanju.
Postgres image mora imati `pgvector`, jer prva migracija kreira `vector`
ekstenziju.

### API sloj

FastAPI aplikacija je srednji sloj izmedju frontend-a i postojecih Python modula
za ETL, parsiranje upita i vektorsku pretragu.

Pokretanje API servera:

```bash
.venv\Scripts\activate
$env:API_TOKEN="ista_vrednost_kao_u_env_fajlu"
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Ako je embedding model vec kesiran lokalno, a okruzenje nema pristup Hugging
Face-u, pre pokretanja API-ja postaviti:

```bash
set HF_HUB_OFFLINE=1
```

Glavne rute:

- `GET /api/health` - provera API-ja i konekcije ka bazi
- `GET /api/stats` - osnovna statistika nad repozitorijumima i publikacijama
- `GET /api/repositories` - lista registrovanih OAI repozitorijuma
- `POST /api/search` - parsiranje prirodnog upita i semanticka pretraga
- `POST /api/auth/register` - registracija admin naloga
- `POST /api/auth/login` - admin login, postavlja `HttpOnly` JWT cookie
- `POST /api/auth/logout` - brise admin cookie-je, uz CSRF proveru
- `GET /api/admin/repositories` - admin lista repozitorijuma sa harvest statusom
- `POST /api/admin/repositories` - kreiranje repozitorijuma iz admin UI-ja
- `PUT /api/admin/repositories/{repo_id}` - izmena naziva, OAI endpoint-a i refresh intervala
- `POST /api/admin/jobs/{job_id}/acknowledge` - sakrivanje zavrsenog job statusa
- `POST /api/admin/repositories/{repo_id}/harvest` - pokretanje harvest-a za repozitorijum
- `GET /api/admin/embeddings` - broj publikacija kojima nedostaje embedding
- `POST /api/admin/embeddings/backfill` - pokretanje embedding backfill procesa

### Frontend

Frontend je React aplikacija u `frontend/` direktorijumu. Tokom razvoja Vite
prosledjuje sve `/api` pozive ka FastAPI serveru koji je definisan kroz
`API_PROXY_TARGET` u root `.env` fajlu i
dodaje `X-API-Key` header iz root `.env` fajla. Token se ne ubacuje u browser
bundle. Admin login koristi JWT token u `HttpOnly` cookie-ju, tako da ga React
kod ne moze procitati iz browser JavaScript-a. React cita samo nesakriveni CSRF
cookie i salje njegovu vrednost nazad kroz `X-CSRF-Token` header za admin
mutacije kao sto su logout, harvest i embedding backfill.

Pokretanje:

```bash
cd frontend
npm install
npm run dev
```

Aplikacija je dostupna na `http://127.0.0.1:5173`.
Frontend koristi `react-router-dom` za glavne stranice:

- `/search` - pretraga
- `/admin` - admin login i admin dashboard

Ove putanje su UI navigacija. Admin zastita je i dalje na FastAPI strani kroz
`HttpOnly` JWT cookie i CSRF proveru.
