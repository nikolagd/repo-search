# GitHub Actions CI proces

Ovaj dokument opisuje CI proces za projekat `repo-search`. Workflow je definisan u fajlu `.github/workflows/ci.yml` i namenjen je javnom GitHub repozitorijumu.

## Cilj

Cilj CI procesa je da GitHub automatski proveri da li aplikacija može uspešno da se build-uje i da li je rezultat build-a validan. Workflow ne radi deployment i ne koristi self-hosted runner na ličnom računaru.

## Grana

Workflow je dodat na feature grani `microservice-architecture`. Grana je izvedena iz `main` grane, pa se promene mogu predati kroz pull request ka `main` ili `master` grani, u zavisnosti od naziva glavne grane u repozitorijumu.

## Okidaci workflow-a

Workflow se pokreće u dva slučaja:

- kada je pull request ka `main` ili `master` grani zatvoren i zaista mergeovan;
- ručno, preko dugmeta `Run workflow` u GitHub Actions tabu.

Konfiguracija okidaca:

```yaml
on:
  pull_request:
    types:
      - closed
    branches:
      - main
      - master
  workflow_dispatch:
```

Svaki job koristi uslov:

```yaml
if: github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true
```

Taj uslov sprečava izvršavanje job-ova kada je pull request samo zatvoren bez mergeovanja, a istovremeno dozvoljava ručno pokretanje workflow-a.

## Runner

Svi job-ovi se izvršavaju na GitHub-hosted Windows runneru:

```yaml
runs-on: windows-latest
```

To znači da se provere izvršavaju na privremenoj Windows virtuelnoj mašini koju obezbeđuje GitHub, a ne na ličnom računaru.

## Javne predefinisane akcije

Workflow koristi javno dostupne GitHub Actions akcije:

- `actions/checkout@v4` za preuzimanje koda iz repozitorijuma;
- `actions/setup-node@v4` za podešavanje Node.js okruženja;
- `actions/setup-python@v5` za podešavanje Python okruženja;
- `actions/upload-artifact@v4` za čuvanje build rezultata;
- `actions/download-artifact@v4` za preuzimanje build rezultata u kasnijem job-u.

## Job-ovi

Workflow ima tri job-a: `frontend-build`, `backend-static-check` i `artifact-verification`.

### frontend-build

Ovaj job build-uje frontend aplikaciju pomoću npm alata.

Koraci:

1. Preuzima kod iz repozitorijuma.
2. Podešava Node.js 22.
3. Instalira zavisnosti komandom `npm ci`.
4. Pokrece build komandom `npm run build`.
5. Proverava da li postoje `frontend/dist/index.html` i generisani fajlovi u `frontend/dist/assets`.
6. Upload-uje `frontend/dist` kao artifact pod nazivom `frontend-dist`.

Direktorijum `frontend/dist` je artifact, odnosno rezultat build procesa.

### backend-static-check

Ovaj job proverava backend/microservice deo aplikacije.

Koraci:

1. Preuzima kod iz repozitorijuma.
2. Podešava Python 3.12.
3. Prolazi kroz sve `.py` fajlove u `microservices` direktorijumu i proverava sintaksu pomoću Python `ast` modula.
4. Proverava da li postoji `docker-compose.microservices.yml`.

Ovaj job ne zavisi od frontend build-a, pa se može izvršavati paralelno sa `frontend-build` job-om.

### artifact-verification

Ovaj job proverava artifact koji je napravljen u `frontend-build` job-u.

Koraci:

1. Preuzima artifact `frontend-dist`.
2. Proverava da li artifact sadrži `index.html`.
3. Proverava da li artifact sadrži generisane asset fajlove.

Ovaj job ima zavisnost:

```yaml
needs:
  - frontend-build
```

Zbog toga se izvršava sekvencijalno, tek nakon uspešnog `frontend-build` job-a.

## Paralelno i sekvencijalno izvršavanje

Paralelno izvršavanje je demonstrirano kroz job-ove:

- `frontend-build`;
- `backend-static-check`.

Oni nemaju `needs` zavisnost, pa ih GitHub Actions može pokrenuti istovremeno.

Sekvencijalno izvršavanje je demonstrirano kroz job `artifact-verification`, koji zavisi od `frontend-build` job-a.

## Ocekivan rezultat

Workflow je uspešan kada:

- `npm ci` uspešno instalira frontend zavisnosti;
- `npm run build` uspešno build-uje frontend;
- postoji build rezultat `frontend/dist/index.html`;
- postoje generisani frontend asset fajlovi;
- Python fajlovi u `microservices` direktorijumu imaju ispravnu sintaksu;
- postoji `docker-compose.microservices.yml`;
- artifact `frontend-dist` može da se upload-uje, preuzme i proveri.

Ako bilo koji od ovih koraka ne prođe, GitHub Actions prikazuje neuspešan workflow i tačan korak na kome je došlo do greške.
