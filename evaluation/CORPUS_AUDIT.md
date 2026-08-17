# Provera korpusa

Komanda `corpus-audit` pravi ponovljiv opis i snimak sačuvanog korpusa publikacija bez vektora, koristeći bazu isključivo za čitanje. Ona ne inicijalizuje šemu, ne preuzima zapise iz repozitorijuma, ne menja publikacije, ne zahteva izradu embedding reprezentacija i ne poziva aplikacione endpoint-e.

## Komanda

Adresa baze može se proslediti neposredno:

```powershell
.\.venv\Scripts\python.exe -m evaluation.corpus_audit --database-url "postgresql://USER:PASSWORD@HOST:PORT/DATABASE" --output-root path\to\audits --embedding-model intfloat/multilingual-e5-large
```

Druga mogućnost je postavljanje promenljive `CORPUS_AUDIT_DATABASE_URL` i izostavljanje argumenta `--database-url`. Adresa i pristupni podaci koriste se samo za uspostavljanje veze i nikada se ne upisuju u izlazne datoteke. Pre prvog upita pokreće se jedna PostgreSQL transakcija `REPEATABLE READ`, `READ ONLY`. Svi upiti koriste istu vezu i transakciju, a komanda izvršava isključivo `SELECT` naredbe.

Svako pokretanje pravi direktorijum `corpus-audit-YYYYMMDDTHHMMSS.ffffffZ` unutar zadatog izlaznog direktorijuma.

## Izlazne datoteke

- `audit.json`: podaci potrebni za ponavljanje postupka, PostgreSQL snimak transakcije, zbirni pokazatelji kvaliteta korpusa, grupe tačnih i mogućih duplikata, polja koja nisu dostupna i ograničenja.
- `repositories.csv`: identitet i konfiguracija repozitorijuma, broj publikacija i poslednji sačuvani harvest posao prema opadajućem vremenu pokretanja i identifikatoru posla.
- `metadata_quality.csv`: po jedan red za svaki repozitorijum i zbirni red `all`, sa pokazateljima kvaliteta i stanja embedding reprezentacija.
- `corpus_snapshot.json`: kanonski UTF-8 snimak koji se koristi za izračunavanje hash vrednosti. Ne sadrži vektore ni pristupne podatke baze.
- `summary.md`: razdvaja izmerene vrednosti, podatke koji nisu dostupni ili nisu sačuvani, moguće duplikate dobijene heuristikom i ograničenja.

## Definicije

- Tekst se smatra nedostajućim ili praznim kada je SQL vrednost `NULL` ili sadrži samo beline.
- Publikacija nema autore ako relacije `publication_author` i `author` ne daju nijedno ime.
- Datum nedostaje kada je SQL vrednost `NULL`.
- Aktuelne, zastarele ili nepoznate i nedostajuće embedding reprezentacije određuju se funkcijom `microservices.common.embedding_provenance.embedding_is_current`, prosleđenim aktivnim modelom i postojećom očekivanom dimenzijom.
- Poslednji harvest posao je poslednje podnet posao: prvi red tipa `repository_harvest` sortiran po identifikatoru repozitorijuma, zatim po `created_at DESC` i opadajućem identifikatoru posla. Noviji posao u stanju čekanja, bez vremena početka, zbog toga se i dalje smatra poslednjim. Polje `job_created_at` se izvozi. Trajanje se računa kao `finished_at - started_at`, a nije dostupno ako bilo koje vreme nedostaje.
- `last_successful_harvest` ostaje nezavisna sačuvana vrednost `repository.last_harvest` i ne zamenjuje se stanjem poslednjeg posla.
- Tačni duplikati OAI identifikatora predstavljaju iste neprazne vrednosti koje se pojavljuju više puta. Jedinstveno ograničenje baze bi u uobičajenom radu trebalo da zadrži ovu vrednost na nuli.
- Mogući duplikati nisu potvrđeni duplikati. Pravilo grupisanja primenjuje Unicode NFKC normalizaciju, svođenje velikih i malih slova i spajanje belina na naslov i izvornu adresu; zahteva neprazan normalizovan naslov; zatim grupiše prema normalizovanom naslovu, tačnom ISO datumu ili njegovom odsustvu i normalizovanoj izvornoj adresi ili njenom odsustvu.
- Izabrani OAI `metadataPrefix` i broj zapisa koje je parser preskočio prikazuju se kao `not recorded`, pošto ih postojeća šema ne čuva. Ova vrednost se razlikuje od numeričke nule.

## Snimak i hash vrednost

Format snimka je `repo-search-corpus-v1`. Publikacije su poređane prema numeričkom identifikatoru, a autori abecedno kao tekstualne vrednosti. Svaki zapis sadrži identifikator publikacije, identifikator repozitorijuma, OAI identifikator, naslov, sažetak, ISO datum, izvornu adresu i autore. Vektori i podaci o njihovom poreklu nisu uključeni.

Objekat snimka serijalizuje se u UTF-8 formatu uz očuvanje Unicode znakova, sortirane ključeve i JSON separatore `,` i `:` bez suvišnih belina. Datoteka `corpus_snapshot.json` sadrži upravo te kanonske bajtove. Njihova SHA-256 vrednost zapisuje se u `audit.json`. Ako se sačuvana polja korpusa ne promene, dobija se ista hash vrednost bez obzira na vreme provere ili početni redosled redova.

## Ograničenja i zamrzavanje stvarnog korpusa

- Provera opisuje sačuvano stanje u jednom PostgreSQL repeatable-read snimku transakcije; ne proverava udaljene OAI repozitorijume i ne zaključuje o harvest ili parser ponašanju koje nije sačuvano.
- `pg_current_snapshot()` se beleži kao PostgreSQL snimak transakcije radi dijagnostike i ponavljanja postupka. On nije spoljna rezervna kopija, tačka povratka ni trajni identifikator snimka baze.
- Identifikator spoljne rezervne kopije ili snimka baze ostaje posebna ručno zadata vrednost pošto ga aplikacija ne može izvesti. Za stvarno zamrzavanje evaluacije potrebno je zaustaviti poslove koji menjaju korpus ili koristiti snimak odnosno repliku baze, a njen spoljni identifikator zabeležiti odvojeno.
- Alat učitava metapodatke publikacija u memoriju radi determinističkog JSON izlaza i klasifikacije porekla vektora i duplikata u Python kodu. Veći budući korpusi mogu zahtevati obradu u toku ili spoljno sortiranje.
- Verzija servera baze beleži se kada je PostgreSQL vrati; nedostajući serverski podaci moraju ostati označeni kao nedostupni, bez izvođenja pretpostavki.

Pre stvarne evaluacije potrebno je zabeležiti Git commit, aktivni embedding model, identifikator snimka ili rezervne kopije baze, direktorijum izlaza provere i dobijenu hash vrednost korpusa. Svih pet datoteka treba arhivirati bez izmena, a kanonski snimak koristiti kao identitet korpusa pri pokretanju pretraga.
