# Izveštavanje nakon ručnog ocenjivanja

Ovaj postupak počinje tek kada ocenjivač završi procenu zaslepljenog skupa kandidata. Program proverava i pretvara unete podatke; ne zaključuje relevantnost, ne popunjava prazna polja, ne usaglašava neslaganja i ne sastavlja zaključke master rada.

Datoteku `runs.json` i rangiranja pojedinačnih metoda ne treba pregledati pre završetka zaslepljenog ocenjivanja. Ručna procena ostaje obavezna.

## 1. Izvoz i uvoz ocena

U radnoj svesci treba popuniti list `Procena`, a zatim ga izvesti kao UTF-8 CSV. Praćena komanda namerno ne čita XLSX i zato ne uvodi dodatnu zavisnost za rad sa tabelama.

Izvorni `candidates.csv` ostaje neizmenjen obrazac skupa. Popunjeni CSV uvozi se komandom:

```powershell
.\.venv\Scripts\python.exe -m evaluation import-judgments `
  --queries path\to\queries.json `
  --pool-template path\to\original-candidates.csv `
  --assessment path\to\completed-assessment.csv `
  --output path\to\judgments.json
```

Argument `--overwrite` koristi se samo kada postojeći izlaz treba namerno zameniti. Izlazna datoteka ne sme biti ista kao neka ulazna datoteka.

Obe CSV datoteke moraju sadržati sledeće kolone upravo ovim redom:

```text
candidate_id,query_text,query_id,publication_id,title,abstract,source_url,relevance
```

Provera se prihvata ili odbija u celini:

- zaglavlja moraju biti potpuno jednaka;
- identifikatori kandidata moraju biti neprazni i jedinstveni;
- svaki par upita i publikacije mora biti jedinstven;
- svaki kandidat iz obrasca mora se tačno jednom pojaviti u ocenjivanju;
- dodatni ili izostavljeni kandidati se odbijaju;
- identifikator kandidata, tekst i identifikator upita, identifikator publikacije, naslov, sažetak i izvorna adresa moraju biti potpuno jednaki nepromenljivom obrascu, uključujući prazna polja metapodataka;
- svaki identifikator i tekst upita moraju odgovarati datoteci `queries.json`;
- polja relevantnosti u obrascu moraju biti prazna;
- ocena relevantnosti mora biti tačno `0`, `1` ili `2`, bez belina, decimalnog zapisa, logičke vrednosti, formule ili objašnjenja.

Fizički redosled redova može se promeniti jer se identitet proverava prema identifikatoru kandidata. Dobijeni `judgments.json` sadrži samo `query_id`, `publication_id` i celobrojni `relevance`. Najpre se pravi i proverava privremena datoteka, a konačan izlaz objavljuje se atomski tek kada cela procena prođe proveru.

## 2. Metapodaci upita

Metapodaci upita prave se prema obrascu `evaluation/templates/query_metadata.template.json`. Šema se nalazi u `evaluation/schemas/query_metadata.schema.json`:

```json
{
  "query_metadata": [
    {
      "query_id": "q1",
      "language": "sr",
      "script": "latin",
      "category": "conceptual",
      "topic": "veštačka inteligencija"
    }
  ]
}
```

Svaki zapis ima tačno polja `query_id`, `language`, `script`, `category` i `topic`. Vrednosti su proizvoljni neprazni tekstualni nizovi, pa srpska latinica, srpska ćirilica, engleski i mešovita terminologija ostaju nepromenjeni. Identifikatori upita moraju biti jedinstveni i tačno obuhvatiti evaluirani skup; nedostajuća, nepoznata, ponovljena ili dodatna polja izazivaju grešku.

Za vrednosti koje se grupišu potrebno je unapred odrediti dosledan način pisanja. Na primer, `sr` i `Serbian` bi bez toga napravili dve različite grupe.

## 3. Detaljan izveštaj

```powershell
.\.venv\Scripts\python.exe -m evaluation report `
  --queries path\to\queries.json `
  --query-metadata path\to\query-metadata.json `
  --judgments path\to\judgments.json `
  --runs path\to\runs.json `
  --output-dir path\to\report `
  --corpus-size 5646 `
  --k 5 10 `
  --embedding-model intfloat/multilingual-e5-large `
  --ranking-config path\to\ranking-config.json `
  --methods language_independent_lexical vector_only full_pipeline
```

Argument `--ranking-config` prihvata i JSON objekat naveden neposredno u komandnoj liniji. Aktuelni Git commit i UTC vreme beleže se automatski. Argument `--overwrite` koristi se samo za namernu zamenu prethodno napravljenog direktorijuma izveštaja.

Konfiguracija rangiranja postaje deo izlaznih metapodataka i zato sme sadržati samo parametre koji nisu poverljivi. Ključevi koji upućuju na tokene, lozinke, tajne, pristupne podatke, administratorske podatke, API ključeve, adrese baze i PostgreSQL URL ili DSN vrednosti se odbijaju.

Komanda zahteva tačno jedno izvršavanje za svaki par upita i metoda, uključujući eksplicitno prazne rezultate. Izveštaj se prvo pravi u susednom privremenom direktorijumu i objavljuje tek po uspešnom završetku svih datoteka.

Izlaz čine:

- `report.json`: mašinski čitljivi metapodaci i svi preseci rezultata;
- `metrics.csv`: zbirne mere uspešnosti po metodu;
- `per_query_metrics.csv`: metapodaci upita, mere uspešnosti, trajanje, režim parsera, broj rezultata, broj pozitivnih ocena i eksplicitna oznaka da nema pozitivnih ocena za svaki par upita i metoda;
- `grouped_metrics.csv`: makro proseci po metodu, grupisani prema jeziku, pismu i kategoriji;
- `latency_summary.csv`: broj izvršavanja i uzoraka, srednja vrednost, medijana, minimum, maksimum i p95 po metodu;
- `parser_mode_summary.csv`: broj i procenat režima parsera, sa eksplicitnom primenljivošću;
- `summary.md`: sažete tabele koje mogu poslužiti kao provereni ulaz za kasnije pisanje master rada.

Grupisane mere uspešnosti računaju se iz istih redova po upitu kao i zbirne mere. Pre grupisanja ponovo se proverava potpuna matrica, pa nijedna grupa ne može neprimetno porediti metode nad različitim skupom upita.

Vrednost p95 trajanja koristi determinističko pravilo najbližeg ranga:

```text
sorted_values[ceil(0.95 * n) - 1]
```

U statistiku trajanja ulaze samo vrednosti koje nisu `null`, dok se ukupan broj, broj izmerenih i broj nedostajućih uzoraka prikazuju odvojeno. Kod keyword i vector-only metoda režim parsera ostaje JSON `null` sa `applicability=not_applicable`. Vrednost `null` u kompletnoj putanji dobija `applicability=unreported`, a prijavljeni režimi zadržavaju stvarne vrednosti. Program ne izmišlja režim parsera.

Uspešnost i trajanje ostaju odvojeni. Ne računa se veštački zbirni rezultat.

## 4. Mere i pravila za ocene

Izveštaj ponovo koristi `evaluation.metrics`:

- Precision@k: broj pozitivno ocenjenih rezultata među prvih k, podeljen sa k;
- Recall@k: broj pozitivno ocenjenih rezultata među prvih k, podeljen ukupnim brojem pozitivnih ocena za taj upit;
- MRR: recipročna vrednost pozicije prvog pozitivno ocenjenog rezultata u celom izvršavanju;
- MRR@k: recipročna vrednost pozicije prvog pozitivno ocenjenog rezultata među prvih k;
- nDCG@k: dobitak `2^relevance - 1` sa logaritamskim umanjenjem prema poziciji, normalizovan idealnim rasporedom dostupnih stepenovanih ocena.

Ocene 1 i 2 smatraju se pozitivnim za Precision, Recall i MRR. nDCG zadržava razliku između ocena 0, 1 i 2. Neocenjene vraćene publikacije tretiraju se kao nerelevantne. Upit bez pozitivnih ocena ostaje u svakom makro proseku i dobija nulu za Recall, MRR i nDCG, uz eksplicitno prikazan status.

Izveštaj beleži Git commit, UTC vreme, veličine korpusa i skupa upita, metode, k vrednosti, model, konfiguraciju rangiranja, SHA-256 ulaza, raspodelu ocena, broj upita bez pozitivnih ocena, broj režima parsera, način računanja percentila i pretpostavke provere. Poverljivi podaci i pristupni podaci servisa ili baze nikada se ne beleže.

## 5. Opciona saglasnost ocenjivača

Kada postoji druga nezavisno popunjena datoteka sa ocenama, pokreće se:

```powershell
.\.venv\Scripts\python.exe -m evaluation agreement `
  --judgments-a path\to\assessor-a.json `
  --judgments-b path\to\assessor-b.json `
  --output-dir path\to\agreement
```

Obe datoteke moraju sadržati isti neprazan skup parova upita i publikacije. Nedostajuće, dodatne, ponovljene ili neispravne ocene se odbijaju. Neslaganja se nikada automatski ne usaglašavaju, a obe izvorne datoteke ostaju nepromenjene.

Izlaz čine `agreement.json`, `confusion_matrix.csv`, `disagreements.csv` i `summary.md`. Matrica zabune 3x3 koristi ocene prvog ocenjivača kao redove, a drugog kao kolone, redom 0, 1 i 2.

Formule su:

- tačna saglasnost: `100 * diagonal_count / pair_count`;
- neponderisana Cohen kappa: `(p_o - p_e) / (1 - p_e)`;
- kvadratna težina: `((grade_a - grade_b) / 2)^2`;
- kvadratno ponderisana kappa: `1 - observed_weighted_disagreement / expected_weighted_disagreement`.

Ako je imenilac kappa mere nula, vrednost nije definisana i u JSON izlazu se zapisuje `null`. Ne zamenjuje se prećutno nulom ili jedinicom. Tačna saglasnost i dalje može da se prikaže. Redovi sa neslaganjima imaju deterministički redosled radi naknadnog razmatranja.

## Ograničenja

- Valjanost zavisi od ljudskih procena relevantnosti; kod proverava strukturu, a ne stručnost ocenjivača ni kvalitet kriterijuma.
- Posmatranje neocenjenih dokumenata kao nerelevantnih može sniziti mere kada je skup kandidata plitak.
- Makro proseci opisuju samo dostavljeni skup upita i zamrznuti korpus.
- Grupisanje prema metapodacima upita zavisi od dosledno održavanog rečnika vrednosti.
- Jedan ocenjivač ne daje dokaz o pouzdanosti između ocenjivača; taj izveštaj je opcioni i zahteva stvarne ocene druge osobe.
- Generisani Markdown predstavlja ulazne dokaze, a ne gotov tekst ili automatski zaključak master rada.
