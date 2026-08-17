# Merenje performansi tokom izvršavanja

Ovaj paket pruža ponovljive alate za merenje vremena pretrage, resursa preko Prometheus-a i embedding backfill obrade. Odvojen je od evaluacije relevantnosti i ne menja ponašanje pretrage, rangiranja, embedding-a niti pozadinskih poslova. Sintetički testovi i sama implementacija nisu rezultati merenja koji se mogu neposredno koristiti u master radu.

## Model identiteta i obavezna provera pre merenja

Svaka komanda proverava identitet pokrenutog sistema pre pokretanja tajmera, Prometheus upita ili pravljenja backfill posla. Izveštaji odvojeno čuvaju četiri pojma:

- `runner_git_commit` je commit radne kopije koja izvršava CLI. On nije dokaz o verziji koja je postavljena u okruženju.
- `verified_deployment_identity` potiče iz obavezne, odvojeno napravljene datoteke sa dokazom deployment-a i njenim SHA-256 hash-om. Beleži postavljenu Git reviziju, nepromenljive image digest vrednosti, vrstu runtime-a, vreme prikupljanja dokaza i podatak da li postavljena revizija odgovara programu koji pokreće merenje.
- `configured_expectations` sadrži naziv modela, embedding revision/template i LLM model zadate u konfiguraciji merenja.
- `observed_runtime_model_identity` sadrži samo četiri opažena polja identiteta modela i UTC vreme njihove provere.

Kod mikroservisa, provera se prijavljuje pomoću `X-API-Key` zaglavlja na podesive Query Service i Embedding Service `/model/status` adrese. Vrednost `llm_model` iz Query Service-a i vrednosti `embedding_model`, `embedding_model_revision` i `embedding_template_version` iz Embedding Service-a porede se sa očekivanjima iz konfiguracije. Nedostajuće, neispravne ili različite vrednosti prekidaju postupak pre merenja. Ostala statusna polja, kao što su URL adrese servisa, uređaji ili poruke o inicijalizaciji, ne prepisuju se u izveštaj.

Aktuelni servisi ne objavljuju Git ili image identitet deployment-a, pa je za svaku komandu potrebna spoljna datoteka sa dokazom deployment-a. Za rezultat spreman za master rad puna postavljena Git revizija mora da bude jednaka `runner_git_commit` vrednosti. `thesis_ready` treba postaviti na `false` samo za dijagnostička pokretanja; nepoklapanje revizija se tada beleži i rezultat se ne sme predstaviti kao konačno merenje.

Stari monolit nema odgovarajući ugovor za prijavljivanje identiteta modela tokom izvršavanja. Zato se provera prekida ako spoljni dokaz ne sadrži sva četiri opažena polja modela. Nedostajući embedding revision ili template nikada se ne dopunjavaju iz radne kopije ili aktuelnih podrazumevanih vrednosti.

Primer konfiguracije mikroservisa:

```json
{
  "deployment_label": "compose-gpu",
  "expected_models": {
    "embedding_model": "intfloat/multilingual-e5-large",
    "embedding_model_revision": "verified-revision",
    "embedding_template_version": "verified-template",
    "llm_model": "verified-llm-model"
  },
  "runtime_identity": {
    "runtime_kind": "microservices",
    "thesis_ready": true,
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "query_model_status_url": "http://localhost:8004/model/status",
    "embedding_model_status_url": "http://localhost:8003/model/status",
    "request_timeout_seconds": 30
  },
  "corpus": {
    "size": 0,
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "search": {
    "endpoint": "http://localhost:8000/api/search",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "warmup_repetitions": 1,
    "measured_repetitions": 10,
    "timeout_seconds": 180,
    "run_classification": "warm",
    "cold_evidence_max_age_seconds": 120
  }
}
```

Obavezan dokaz deployment-a za mikroservise:

```json
{
  "deployment_label": "compose-gpu",
  "runtime_kind": "microservices",
  "deployment_git_revision": "<full deployed Git revision>",
  "image_identities": {
    "query-service": "registry.example/query@sha256:<64 hexadecimal characters>",
    "embedding-service": "registry.example/embedding@sha256:<64 hexadecimal characters>"
  },
  "captured_at_utc": "2026-01-01T09:59:00Z",
  "source": "external deployment inspection record"
}
```

Za `runtime_kind: legacy_monolith`, odeljak `runtime_identity` sadrži samo `runtime_kind` i `thesis_ready`. Datoteka sa dokazom ima ista osnovna polja i dodatni odeljak:

```json
{
  "observed_runtime_models": {
    "embedding_model": "externally verified model",
    "embedding_model_revision": "externally verified revision",
    "embedding_template_version": "externally verified template",
    "llm_model": "externally verified LLM"
  }
}
```

## CLI i izlazne datoteke

API tokeni prihvataju se samo kroz nazive environment promenljivih iz konfiguracije. Ne postoji CLI argument za neposredno prosleđivanje tokena. Polja za pristupne podatke i URL adrese koje ih sadrže odbijaju se, a tačne vrednosti tokena proveravaju se pre objavljivanja izlaza.

```powershell
$env:PERFORMANCE_API_TOKEN = "<runtime token>"
.\.venv\Scripts\python.exe -m performance_measurement search --config .\config.json --deployment-evidence .\deployment-evidence.json --queries .\queries.json --output-dir .\.local-artifacts\performance\compose-search
.\.venv\Scripts\python.exe -m performance_measurement resources --config .\config.json --deployment-evidence .\deployment-evidence.json --output-dir .\.local-artifacts\performance\compose-resources
.\.venv\Scripts\python.exe -m performance_measurement backfill --config .\config.json --deployment-evidence .\deployment-evidence.json --output-dir .\.local-artifacts\performance\compose-backfill
Remove-Item Env:PERFORMANCE_API_TOKEN -ErrorAction SilentlyContinue
```

Svaki izlazni direktorijum sadrži `measurement.json`, `samples.csv`, `summary.md` i `SHA256SUMS`. Prvo se pravi privremeni susedni direktorijum, a rezultat se objavljuje atomski. Postojeći izlaz je zaštićen osim kada je eksplicitno prosleđen `--overwrite`. Direktorijum `.local-artifacts/performance` rezervisan je za odobrena stvarna merenja i Git ga ne prati.

Ulaz sa upitima za pretragu strogo se proverava, a tekst upita se ne prepisuje u izlaz:

```json
{"queries": [{"id": "q01", "query": "example search", "limit": 10}]}
```

## Vreme pretrage i dokaz hladnog pokretanja

Zahtevi za pretragu izvršavaju se redom i mere pomoću `time.perf_counter_ns`. Izvorni redovi čuvaju fazu, klasifikaciju, query ID, broj ponavljanja, ishod/status, HTTP status, vreme u nanosekundama i milisekundama, broj rezultata, parser režim i opštu kategoriju greške. Warm-up redovi nikada ne ulaze u statistiku merenja. Neuspešni zahtevi ostaju vidljivi u izvornim redovima, ali se izostavljaju iz sažetka vremena.

`warm` je uobičajena klasifikacija nakon zagrevanja. `first_request` zahteva nula warm-up ponavljanja i ne tvrdi da je sistem hladan. `cold` zahteva nula warm-up ponavljanja i odvojenu datoteku sa dokazom restartovanja i readiness provere:

```json
{
  "deployment_label": "compose-gpu",
  "source": "external restart and readiness log",
  "restart_completed_at_utc": "2026-01-01T10:00:00Z",
  "readiness_confirmed_at_utc": "2026-01-01T10:01:00Z"
}
```

Readiness mora biti potvrđen nakon restartovanja, vreme ne sme biti u budućnosti i dokaz na početku merenja ne sme biti stariji od `cold_evidence_max_age_seconds`. Dozvoljena i opažena starost readiness dokaza čuvaju se u izveštaju. Prvi zahtev posle važećeg dokaza označava se kao `cold`, a naredni kao `warm`.

Sažeci sadrže broj pokušanih, uspešnih i neuspešnih zahteva, kao i srednju vrednost, medijanu, minimum, maksimum, p50 i p95. Percentili koriste deterministički nearest-rank postupak: `sorted_values[ceil(p*n)-1]`.

## Resursi preko Prometheus-a

Prometheus instant/range definicije imaju nazive i u potpunosti su podesive; alat nema hard-coded deployment oznake. Svaka definicija sme da vrati najviše jednu seriju. Ako ih vrati više, pokretanje se prekida uz zahtev da se PromQL izraz agregira ili preciznije ograniči. Vrednosti različitih skupova label-a zato se nikada ne objedinjuju.

Primer odeljka konfiguracije:

```json
{
  "prometheus": {
    "base_url": "http://localhost:9090",
    "timeout_seconds": 30,
    "metrics": [
      {
        "name": "query_cpu_rate",
        "metric_type": "cpu",
        "unit": "cores",
        "query_kind": "query",
        "query": "sum(rate(container_cpu_usage_seconds_total{container=\"query-service\"}[1m]))"
      }
    ]
  }
}
```

Ako Prometheus zahteva autentifikaciju, u ovom odeljku treba postaviti `api_token_env`. Token se šalje kao bearer token i nikada se ne zapisuje u izveštaj.

Za svaku metriku beleže se prihvaćeni skup label-a i broj serija. Čuvaju se i izvorno vreme, vrednost i label-e. Prazni ili neuspešni upiti za CPU, RAM, iskorišćenost GPU-a ili GPU framebuffer označavaju se kao `unavailable` sa null sažetkom, a nikada kao nula. Numeričke vrednosti koje nisu konačne čine pokretanje nevažećim.

## Embedding backfill i uporedivost

Provera pre backfill merenja završava se pre nego što komanda napravi jedan postojeći Job Service posao tipa `embedding_backfill` i prati njegov ID do završnog stanja. Beleže se opaženo vreme u redu, početak i završetak u servisu, broj pokušaja, broj obrađenih zapisa, trajanje prema servisu i merenju i broj zapisa u sekundi. Alat ne pokreće harvest i ne pravi veštački zastarele embeddings. Pošto Job Service vremenske oznake iz PostgreSQL baze nemaju podatak o vremenskoj zoni, konfiguracija mora eksplicitno da sadrži `job_timestamp_timezone: UTC`.

Primer odeljka konfiguracije:

```json
{
  "backfill": {
    "job_service_url": "http://localhost:8002",
    "api_token_env": "PERFORMANCE_API_TOKEN",
    "poll_interval_seconds": 1,
    "timeout_seconds": 3600,
    "request_timeout_seconds": 30,
    "job_timestamp_timezone": "UTC"
  }
}
```

Compose, Kubernetes i monolitna merenja mogu da se porede samo kada se poklapaju dokaz deployment-a, hash vrednosti upita i konfiguracije, revizije programa za merenje i postavljenog sistema, identitet korpusa, podešeni i opaženi modeli, broj ponavljanja, semantika endpoint-a, PromQL obuhvat i periodi merenja. Scrape intervali, readiness kriterijumi, opterećenje računara, stanje keša, GPU takt i starost cold dokaza moraju se posebno pregledati za svako buduće stvarno merenje.
