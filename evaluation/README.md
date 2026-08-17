# Osnova za evaluaciju pretrage

Ovaj paket omogućava poređenje zamrznutog skupa od tri metode i opcione četvrte, jezički prilagođene leksičke metode. Paket ne određuje teme evaluacije, ocene relevantnosti niti rezultate za master rad. Datoteke u direktorijumu `templates/` namerno su prazne. Stvarni upiti i ocene moraju da se pripreme i pregledaju odvojeno.

Prikupljanje rezultata iz pokrenutog sistema opisano je u `COLLECT_RUNS.md` i dostupno je kroz komandu `python -m evaluation collect-runs`.

Za reprodukciju starijih eksperimenata, `python -m evaluation.bm25_artifacts` i dalje pravi sirovo `bm25` poređenje. Generator `python -m evaluation.language_independent_lexical_artifacts` pravi novi izolovani direktorijum sa datotekama `language-independent-lexical-runs.json`, `runs.json` za metode `language_independent_lexical`/`vector_only`/`full_pipeline`, zaslepljenim skupom `candidates.csv` i kompletnim `metadata.json`. Obe komande zahtevaju očekivane SHA-256 vrednosti i odbijaju da prepišu postojeći izlazni direktorijum.

Uvoz ručnih ocena, detaljno izveštavanje i opciono poređenje ocenjivača opisani su u `REPORTING.md`. Ručna procena relevantnosti je obavezna; kod nikada ne pravi niti zaključuje ocene.

Zavisnosti namenjene evaluaciji moraju biti odvojene od production zahteva. Iz korena repozitorijuma treba instalirati `requirements-evaluation.txt` u okruženju za evaluaciju ili CI:

    python -m pip install -r requirements-evaluation.txt

Ova datoteka uključuje `requirements-ci.txt`, gde je fiksirana verzija `snowballstemmer==3.1.1`. Zavisnosti za evaluaciju ne treba instalirati u production image; `requirements.txt` namerno ne sadrži Snowball stemmer.

## Metode

- `keyword`: stara deterministička osnova zasnovana na učestalosti tokena. Tekst prolazi kroz Unicode NFKC normalizaciju i case folding, a zatim se deli na Unicode reči. Radi kompatibilnosti sa starim zamrznutim artefaktima, rezultat ostaje `2 * učestalost termina upita u naslovu + učestalost termina upita u sažetku`. Metoda nije deo konačnog skupa evaluacije.
- `bm25`: istorijska sirova leksička metoda. Biblioteka `bm25s==0.3.10` primenjuje BM25 računanje po Lucene postupku sa `k1=1.2` i `b=0.75`. Metoda je zadržana radi tačne reprodukcije ranijeg skupa kandidata.
- `language_independent_lexical`: jezički nezavisna leksička kontrola iz ranijeg protokola. Spaja BM25 rang nad Unicode rečima i BM25 rang nad četvorogramima karaktera unutar reči pomoću reciprocal-rank fusion postupka sa fiksnim `k=60`. Obe komponente koriste `k1=1.2`, `b=0.75` i formulu `2.0 * BM25 naslova + BM25 sažetka`. Jezička nezavisnost odnosi se samo na pripremu teksta: nema prevođenja, preslovljavanja, semantičke ekvivalencije, stemming-a, lematizacije ni liste stop reči. Srpski upit uglavnom ne može da pronađe dokument samo na engleskom ako nemaju zajedničke površinske oblike.
- `language_aware_lexical`: proširenje prethodne metode namenjeno isključivo evaluaciji. Zadržava precizni kanal originalnih reči i četvorograme unutar tokena, a dodaje jezički prilagođen BM25 kanal. Srpske putanje koriste deklarisane metapodatke upita (`language` i `script`) za kanonsko preslovljavanje ćirilice u latinicu, varijante srpske latinice bez dijakritika i fiksiranu verziju Snowball stemmer-a za srpski. Engleske putanje koriste Snowball stemmer za engleski, dok jedina `Serbian_mixed` putanja deterministički primenjuje oba. Label-blind provera pokrivenosti pojmova koristi fiksne i verzionisane skupove srpskih i engleskih funkcionalnih reči. Zahteva jedno poklapanje za upite sa jednim ili dva pojma, odnosno `ceil(0.4 * concept_count)` za duže upite, a po potrebi deterministički dopunjava listu leksičkim kandidatima ispod praga. Težine RRF komponenti su 1.0 za precizne reči, 1.0 za jezički prilagođeno poređenje i 0.5 za četvorograme, uz `k=60`. Normalizovane stop liste, njihov hash, pravilo formiranja pojmova i prag beleže se u metapodacima metode. Ne koriste se sinonimi, lematizacija, vektorska pretraga niti LLM prepoznavanje jezika.
- `vector_only`: jednom pravi embedding originalnog upita i poziva postojeću granicu za vektorsku pretragu, bez LLM parsiranja, phrase boost-a, spajanja kandidata i query-coverage boost-a. Embedder i funkcija za čitanje rezultata prosleđuju se kao zavisnosti, što omogućava determinističke testove i korišćenje postojećih Search/Embedding funkcija.
- `full_pipeline`: koristi odgovor aplikacionog Search Service-a, uključujući konačne score vrednosti i `plan.parser_mode`. Testovi prosleđuju determinističku zamensku funkciju, pa se Ollama tokom njih ne poziva.

## Mašinski čitljivi formati

Svi identifikatori su string vrednosti kako se ne bi uvodile pretpostavke o budućim izvorima korpusa.

- Upiti (`queries.json`): `{"queries": [{"query_id": "...", "text": "..."}]}`.
- Ocene (`judgments.json`): `{"judgments": [{"query_id": "...", "publication_id": "...", "relevance": 0|1|2}]}`, gde je 0 nerelevantno, 1 delimično relevantno, a 2 relevantno.
- Pokretanja (`runs.json`): `{"runs": [...]}` sa jednim objektom za svaki par upita i metode. Svaki zapis čuva `query_id`, `method`, opciono `latency_ms`, opciono `parser_mode` i niz `results`. Rezultati sadrže neprekinuti rang koji počinje od jedan, `publication_id`, numerički `score` i opciona polja za prikaz. Pokretanje bez rezultata zapisuje se kao `"results": []` i ne izostavlja se.
- Metapodaci upita (`query-metadata.json`): potpuna evidencija upita sa jezikom, pismom, kategorijom i temom.
- Detaljan izveštaj (`report.json`): podaci potrebni za reprodukciju, agregatni i pojedinačni rezultati, grupisanja, vremena i parser režimi. Odgovarajuće CSV datoteke i `summary.md` objavljuju se atomski.

Šeme su u direktorijumu `schemas/`, a prazne početne datoteke u `templates/`. Sintetički test podaci postoje samo u `tests/` i ne predstavljaju rezultate evaluacije.

Pronađeni dokumenti bez ocene tretiraju se kao nerelevantni. Upit bez pozitivnih ocena dobija nulu za Recall, MRR i nDCG; Precision je takođe nula ako nema pozitivno ocenjenog rezultata. Takvi upiti ostaju u macro proseku i posebno se broje u `queries_without_relevant_judgments`.

I objedinjavanje kandidata i izveštavanje očekuju tačno jedno pokretanje za svaki upit i metodu. Radi kompatibilnosti, podrazumevani skup i dalje čine `language_independent_lexical`, `vector_only` i `full_pipeline`; za dodatno poređenje treba eksplicitno proslediti `language_aware_lexical`. Istorijski `bm25` i stari `keyword` dostupni su samo kada se izričito navedu. Dupli argumenti metoda i nedostajući, dupli ili nepoznati parovi upita i metode odbijaju se, pa svi agregati koriste isti skup upita, uključujući pokretanja bez rezultata. Odbijaju se i dupli ID-evi upita, ocene, publication ID-evi i rangovi, rangovi sa prazninama ili početkom različitim od jedan, nepoznate reference, neograničene numeričke vrednosti i negativno ili neograničeno vreme.

Generator jezički prilagođenih artefakata zaslepljuje top-five skup determinističkim SHA-256 identifikatorima izvedenim samo iz para `query_id`/`publication_id`. Grupe upita ostaju zajedno radi praktičnog ocenjivanja, dok se redovi unutar grupe mešaju zabeleženim seed-om. Redosled i candidate ID ne otkrivaju rang. Provera pokrivenosti pojmova je label-blind i čita samo zamrznute upite, metapodatke, tekst korpusa i pronađene redove.

## Metrike

- Precision@k: broj pozitivno ocenjenih dokumenata u prvih k pozicija podeljen sa k.
- Recall@k: broj pozitivno ocenjenih dokumenata u prvih k pozicija podeljen ukupnim brojem pozitivno ocenjenih dokumenata za upit.
- MRR: recipročna vrednost ranga prvog pozitivno ocenjenog rezultata u celom prosleđenom pokretanju.
- MRR@k: recipročna vrednost ranga prvog pozitivno ocenjenog rezultata samo unutar prvih k pozicija.
- nDCG@k: DCG sa gain vrednošću `2^relevance - 1` i logaritamskim umanjenjem, podeljen idealnim redosledom dostupnih stepenovanih ocena.

Protokol zamenskog skupa kandidata opisan je u `LANGUAGE_INDEPENDENT_LEXICAL_BASELINE.md`; `FINAL_BM25_PROTOCOL.md` je sačuvan kao istorijski sirovi BM25 protokol. Generički izveštaj i dalje ispisuje Recall i neograničeni MRR radi ponovne upotrebe, ali ta polja nisu podržani zaključci protokola sa dubinom 5.

## Komande

Pravljenje determinističkog skupa za ocenjivanje u kom su metode skrivene. Izlaz namerno ne sadrži nazive metoda ni podatak kojoj metodi kandidat pripada:

```powershell
.\.venv\Scripts\python.exe -m evaluation candidate-pool --queries path\to\queries.json --runs path\to\runs.json --output path\to\candidates.csv --depth 10 --seed 2026 --methods language_independent_lexical vector_only full_pipeline
```

Prikupljanje samo jezički prilagođene metode uz zamrznute metapodatke upita:

```powershell
.\.venv\Scripts\python.exe -m evaluation collect-runs --queries path\to\queries.json --query-metadata path\to\query-metadata.json --output path\to\runs.json --methods language_aware_lexical --limit 20 ...
```

Nakon završetka ručnog ocenjivanja, list za procenu treba izvesti kao UTF-8 CSV i proveriti/uvoziti:

```powershell
.\.venv\Scripts\python.exe -m evaluation import-judgments --queries path\to\queries.json --pool-template path\to\original-candidates.csv --assessment path\to\completed-assessment.csv --output path\to\judgments.json
```

Zatim se pravi detaljan izveštaj:

```powershell
.\.venv\Scripts\python.exe -m evaluation report --queries path\to\queries.json --query-metadata path\to\query-metadata.json --judgments path\to\judgments.json --runs path\to\runs.json --output-dir path\to\report --corpus-size 1000 --k 5 10 --embedding-model intfloat/multilingual-e5-large --ranking-config '{"candidate_multiplier":6}' --methods language_independent_lexical vector_only full_pipeline
```

Opciono poređenje dva ocenjivača:

```powershell
.\.venv\Scripts\python.exe -m evaluation agreement --judgments-a path\to\assessor-a.json --judgments-b path\to\assessor-b.json --output-dir path\to\agreement
```

Izveštaj beleži aktuelni Git commit ako `--git-commit` nije prosleđen, UTC vreme, veličinu korpusa i skupa upita, metode, k vrednosti, model, konfiguraciju rangiranja, hash vrednosti ulaza, broj ocena, parser režime, sažetak vremena i pretpostavke validacije. Formule, pravila provere, atomsko objavljivanje i ograničenja opisani su u `REPORTING.md`.

## Prikupljanje stvarnih dokaza

1. Definisati stvarne informacione potrebe pre pregleda rezultata sistema. Dodeliti stabilne query ID vrednosti i zabeležiti tekst upita bez prilagođavanja pojedinačnoj metodi.
2. Pokrenuti sve metode nad istim zamrznutim korpusom i konfiguracijom, uz čuvanje rangova, score vrednosti, vremena, parser režima, Git commit-a i podataka o modelima i rangiranju.
3. Izvesti objedinjeni skup kandidata sa zabeleženom dubinom i seed vrednošću. Ocenjivaču dati zaslepljeni skup, a ne runs pojedinačnih metoda.
4. Bez pregleda method-specific `runs.json`, kvalifikovani ocenjivač dodeljuje ocene 0/1/2 prema pisanom kriterijumu relevantnosti. Ocene koje nedostaju ne smeju se zaključivati iz ranga ili score-a.
5. Duple ili konfliktne procene usaglasiti dokumentovanim postupkom, sačuvati izvorne procene i zatim napraviti konačnu datoteku ocena.
6. Napraviti i arhivirati mašinski čitljiv izveštaj. Zaključci važe samo za zabeleženi korpus, upite, ocene i verziju sistema.
