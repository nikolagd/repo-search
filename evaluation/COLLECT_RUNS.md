# Prikupljanje stvarnih rezultata evaluacije pretrage

Komanda `collect-runs` izvršava unapred pripremljen UTF-8 skup upita nad zamrznutim korpusom i pravi datoteku `runs.json` usklađenu sa šemom. Ona ne sastavlja upite, ne određuje ocene relevantnosti i ne pravi mere, skupove kandidata niti zaključke master rada.

## Granice metoda

- `keyword` koristi `KeywordBaselineAdapter` nad metapodacima publikacija učitanim iz zamrznute baze. Postojeće NFKC svođenje, poređenje bez obzira na veličinu slova, bodovanje prema učestalosti tokena i sortiranje prema rezultatu pa tekstualnom identifikatoru ostaju nepromenjeni. Reč je o internoj osnovi, a ne reprodukciji DSpace, Google Scholar, PostgreSQL full-text ili nekog drugog pretraživača.
- `language_independent_lexical` je leksički metod korišćen pri formiranju završnog skupa kandidata. Spaja BM25 rangiranje Unicode reči i BM25 rangiranje karakterskih četvorograma unutar reči primenom reciprocal-rank fusion postupka sa fiksnim `k=60`. Obe komponente koriste fiksiranu verziju `bm25s==0.3.10`, varijantu `lucene`, parametre `k1=1.2` i `b=0.75`, odvojene indekse naslova i sažetka i izraz `2.0 * title BM25 + abstract BM25`. Metod nije međujezička pretraga: ne prevodi niti povezuje srpske i engleske izraze. Raniji sirovi `bm25` ostaje dostupan radi ponavljanja prethodnog postupka.
- `language_aware_lexical` je proširenje namenjeno samo evaluaciji. Zadržava prethodne kanale tačnih izvornih reči i karakterskih četvorograma unutar tokena, a dodaje pomoćni jezički prilagođen BM25 kanal. Putanja se bira iz metapodataka upita (`language` i `script`), a ne LLM detekcijom. Srpske putanje preslovljavaju ćirilicu u preciznu srpsku latinicu, dodaju latinične varijante bez dijakritika i primenjuju srpski algoritam iz `snowballstemmer==3.1.1`. Engleske putanje primenjuju engleski algoritam, dok eksplicitna putanja `Serbian_mixed` primenjuje oba. Pokrivenost koristi fiksirane i verzionisane skupove funkcionalnih reči za srpski i engleski samo pri određivanju različitih pojmova sastavljenih od reči, nikada za karakterske n-grame. Primarni kandidati ispunjavaju prag `1`/`ceil(0.4 * concept_count)`, a deterministička dopuna rezultatima ispod praga koristi se samo kada je potrebna. Težine RRF spajanja su `1.0` za tačne reči, `1.0` za jezički prilagođeno poređenje i `0.5` za karakterske četvorograme, uz `k=60`. Izvorni precizni tokeni ostaju u indeksu; ne dodaju se sinonimi, lematizacija, vektorska pretraga ni promene produkcione pretrage.
- `vector_only` šalje izvorni upit neposredno endpoint-u `/embed/query` servisa Embedding Service, ne poziva Query Service i izvršava zajedničku produkcionu pgvector funkciju bez godina, fraza, pojačavanja rezultata, spajanja kandidata i pravila pokrivenosti. Evaluacija dodaje `publication.id ASC` samo kao determinističko pravilo kod jednake udaljenosti; produkciona pretraga zadržava postojeće ponašanje.
- `full_pipeline` šalje neizmenjen upit Gateway endpoint-u `/api/search`. Gateway, Search, Query i Embedding servisi zaduženi su za tumačenje i rangiranje. Prikupljač čuva vraćeni redosled, rezultate i `plan.parser_mode`, bez ponovne implementacije rangiranja.

Svaki rezultat mora da upućuje na publikaciju učitanu iz proverene zamrznute transakcije. Kod metoda `full_pipeline` vraćeni naslov i izvorna adresa moraju biti jednaki zamrznutim vrednostima, uključujući dozvoljene vrednosti `null`. Time se sprečava da Gateway povezan sa drugom bazom neprimetno unese rezultate.

Metodi keyword, BM25 i vector-only dele jednu PostgreSQL transakciju `REPEATABLE READ`, `READ ONLY`. Kompletna putanja koristi transakcije kojima upravljaju servisi, pa se doslednost preko te granice čuva zaustavljanjem komponenti koje menjaju korpus i proverom korpusa pre i posle izvršavanja.

Jezički nezavisni leksički metod predstavlja ponovljivu klasičnu leksičku osnovu nad zamrznutim lokalnim korpusom. Unicode analiza izbegava vezivanje za jedan jezik, ali ne dodaje višejezičko razumevanje ni semantičku jednakost. Ne tvrdi se da ovaj metod reprodukuje Google Scholar ili DSpace/Solr konfiguraciju izvornih repozitorijuma. Aktivna RFOS/REPFF pretraga nije osnovni metod za poređenje pošto se njeni indeksi i podešavanja mogu menjati, a sirovi rezultati dva repozitorijuma ne mogu se neposredno spojiti.

## Analizator i zavisnosti evaluacije

Jezički prilagođeni kanal emituje svaku kanonsku, uprošćenu ili zadatu stem varijantu najviše jednom za svako pojavljivanje izvornog tokena. Ponovljena pojavljivanja izvornog tokena ostaju ponovljena; tokeni se ne uklanjaju na nivou dokumenta, tako da učestalost izraza i dužina dokumenta u BM25 i dalje imaju smisla. Kanali izvornih reči i karaktera ostaju nepromenjeni.

Analizator pokrivenosti pravi jednu grupu za svaki izvorni token upita koji nije funkcionalna reč i čuva precizne, kanonske, pojednostavljene i odgovarajuće stem varijante. Isti identitet pojma računa se jednom. Tačni sirovi i normalizovani skupovi funkcionalnih reči, zajedno sa njihovim SHA-256 vrednostima, beleže se u `language_aware_lexical_metadata`. Karakterski četvorogrami nikada ne stvaraju pojmove.

Zavisnosti namenjene evaluaciji instaliraju se iz korena repozitorijuma:

    python -m pip install -r requirements-evaluation.txt

Fiksirana Snowball zavisnost namerno se nalazi u `requirements-ci.txt`, a ne u `requirements.txt` koji koriste produkcione slike.

## Potrebna konfiguracija okruženja

- Podrazumevano `EVALUATION_DATABASE_URL`, odnosno promenljiva čiji je naziv prosleđen argumentom `--database-url-env`. Mora da pokazuje na istu zamrznutu primarnu bazu koju koristi Search Service.
- Podrazumevano `EVALUATION_API_TOKEN`, odnosno promenljiva čiji je naziv prosleđen argumentom `--api-token-env`. Vrednost se šalje samo u zaglavlju `X-API-Key`.
- Osnovna adresa servisa Embedding Service i adresa Gateway endpoint-a `/api/search`, prosleđene kao argumenti komandne linije.
- Očekivana veličina korpusa, SHA-256 kanonskog snimka korpusa i aktivni embedding model.
- Argument `--query-metadata` je obavezan kada se koristi `language_aware_lexical` i mora tačno jednom obuhvatiti svaki upit.

Adrese baze, lozinke, API tokeni, JWT vrednosti i administratorski pristupni podaci ne upisuju se u rezultate, ne ispisuju se i ne uključuju u prečišćene poruke o greškama. JWT i administratorski podaci nisu potrebni. Datoteku `.local-artifacts/evaluation/credentials.local.txt` ne treba koristiti.

## Postupak sa zamrznutim okruženjem `repo-search-eval`

Praćena datoteka `evaluation/docker-compose.collect-runs.yml` dodaje samo privremeni izvršni kontejner postojećem Compose projektu `repo-search-eval`. Koristi već napravljenu sliku Search Service-a zbog zavisnosti i montira aktuelnu radnu kopiju samo za čitanje na `/workspace`. Zbog toga učitava `evaluation` i `microservices` iz trenutne grane iako ih `Dockerfile.microservice` ne kopira zajedno. Kontejner ne objavljuje portove. PostgreSQL, Embedding Service i Gateway dostupni su preko postojeće Compose mreže.

Koristi se postojeća ignorisana datoteka `.local-artifacts/evaluation/repo-search-eval.env`; pristupne podatke ne treba prepisivati u samu komandu. Potrebno je napraviti kontrolisane ignorisane ulazne i izlazne direktorijume i nezavisno sastavljen UTF-8 skup upita sačuvati kao `.local-artifacts/evaluation/queries/queries.json`:

```powershell
New-Item -ItemType Directory -Force .local-artifacts\evaluation\queries, .local-artifacts\evaluation\runs
```

Obe komponente koje mogu menjati podatke moraju ostati zaustavljene. Ako je Docker ponovo pokrenut, pokreće se samo već napravljena putanja za čitanje; komanda `start` ne pravi iznova zamrznute kontejnere i volumene. Pre nastavka treba sačekati da PostgreSQL, Ollama, Embedding Service, Query Service, Search Service i Catalog Service budu spremni. Zbirna Gateway provera može ostati neuspešna samo zato što je Job Service namerno zaustavljen.

```powershell
docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml stop job-worker job-service

docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml start db-primary ollama embedding-service query-service catalog-service search-service gateway

docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml ps
```

Prikupljač se pokreće tek kada navedeni servisi putanje za čitanje budu spremni:

```powershell
docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml run --rm --no-deps evaluation-runner collect-runs `
  --queries /evaluation-input/queries.json `
  --output /evaluation-output/runs.json `
  --methods language_independent_lexical vector_only full_pipeline `
  --limit 20 `
  --database-url-env EVALUATION_DATABASE_URL `
  --api-token-env EVALUATION_API_TOKEN `
  --embedding-service-url http://embedding-service:8000 `
  --full-pipeline-url http://gateway:8000/api/search `
  --request-timeout 180 `
  --embedding-model intfloat/multilingual-e5-large `
  --expected-corpus-size 5646 `
  --expected-snapshot-hash b366854b50c7abb40b51c29a943f89fdd22b0af33cac6b6cd3371ff2404eebce
```

Datoteka `evaluation/docker-compose.collect-runs.yml` sastavlja adresu baze u privatnoj mreži i mapira API token samo iz promenljivih ignorisane environment datoteke. Ulazni direktorijum montiran je samo za čitanje; upis je dozvoljen jedino u `.local-artifacts/evaluation/runs`. Korenski sistem datoteka izvršnog kontejnera takođe je samo za čitanje, a kontejner se uklanja posle rada. Prikupljač otvara PostgreSQL u režimu `REPEATABLE READ, READ ONLY`. Argument `--overwrite` mora biti eksplicitno zadat kada se menja postojeći izlaz.

Kompletna konfiguracija može se proveriti, a pokretanje aktuelne radne kopije unutar izvršnog kontejnera potvrditi bez izvršavanja upita i pregleda rangiranja:

```powershell
docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml config --quiet

docker compose --env-file .local-artifacts/evaluation/repo-search-eval.env --project-name repo-search-eval -f docker-compose.microservices.yml -f .local-artifacts/evaluation/docker-compose.eval.override.yml -f evaluation/docker-compose.collect-runs.yml run --rm --no-deps evaluation-runner --help
```

## Provera i ponašanje pri grešci

- Upiti moraju imati jedinstvene neprazne tekstualne identifikatore i neprazan tekst. Tekst se čita i upisuje kao UTF-8 bez preslovljavanja i ASCII konverzije.
- Metodi moraju biti jedinstveni članovi skupa `keyword`, `bm25`, `vector_only`, `full_pipeline`, `language_independent_lexical` i `language_aware_lexical`. Završni skup zadaje se eksplicitno, dok se `keyword` koristi samo kada je potreban istorijski postupak.
- Ograničenje je od 1 do 50, u skladu sa Search API-jem aplikacije.
- Pre pretrage proveravaju se veličina korpusa, hash kanonskog snimka, aktuelno poreklo svake embedding reprezentacije, model servisa Embedding Service i dimenzija vektora.
- Svaki izabrani par upita i metoda daje tačno jedno izvršavanje, uključujući eksplicitno prazne rezultate.
- Identifikatori, prikazana polja, rezultati, trajanje, režim parsera, duplikati i identitet metoda i upita proveravaju se u svom kontekstu. Svaki rezultat mora pripadati zamrznutom korpusu, a naslov i izvorna adresa kompletne putanje moraju odgovarati zamrznutoj publikaciji, uključujući dozvoljene prazne vrednosti. Završna provera šeme i matrice poređenja ponovo koristi `evaluation.io`.
- Nova veza baze namenjena samo čitanju ponavlja proveru korpusa posle poziva servisa. Izlaz se pravi tek nakon te provere.
- Izlaz koristi privremenu datoteku u istom direktorijumu i atomski `os.replace`. Neuspešan upit navodi identifikator upita i metod, ne ostavlja delimičnu novu datoteku i nikada prećutno ne izostavlja izvršavanje.

Prikupljač ne upisuje podatke o modelu i rangiranju u `runs.json`, pošto postojeća šema sadrži samo izvršavanja. Search Service upravlja konfiguracijom rangiranja; ona se beleži odvojeno prilikom pokretanja postojeće komande za izveštavanje.

## Zaštita zamrznutog korpusa i ograničenja

- Job Worker i Job Service moraju ostati zaustavljeni. Tokom prikupljanja ne pokreću se harvest ni backfill poslovi.
- Zbirni Gateway health endpoint očekivano može biti neuspešan dok je Job Service zaustavljen; relevantan je endpoint `/api/search`.
- Timeout prikupljača ne može produžiti interna ograničenja Search-to-Query veze od 90 sekundi ni Gateway prosleđivanja od 120 sekundi.
- Provera kanonskih metapodataka i porekla vektora pre i posle rada otkriva promene metapodataka ili porekla, ali ne može otkriti izmenjenu vrednost vektora ako podaci o poreklu ostanu isti.
- Rangirani izlaz ne sme se pregledati radi sastavljanja upita. Upiti se završavaju nezavisno pre prikupljanja, a relevantnost se kasnije određuje iz zaslepljenog skupa kandidata.
