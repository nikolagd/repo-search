# Jezički nezavisna leksička osnova za evaluaciju

## Uloga i granice tvrdnje

Završni skup kandidata koristi mašinski identifikator metoda `language_independent_lexical` (verzija `1.0`) uz nepromenjena izvršavanja `vector_only` i `full_pipeline`. U master radu ovaj metod se naziva **jezički nezavisna leksička osnova**.

Reč je o klasičnom, strogo leksičkom metodu. Boduje zajedničke normalizovane oblike reči i zajedničke delove karaktera. Ne koristi embedding reprezentacije, LLM, mašinsko prevođenje, preslovljavanje, rečnike, semantičko proširenje upita, cross-encoder, povratnu informaciju o relevantnosti ni pravila vezana za pojedinačne upite. Izraz „jezički nezavisna” označava samo da jedan Unicode analizator može da obradi srpsku ćirilicu, srpsku latinicu i engleski tekst iz ovog korpusa bez prethodnog određivanja jezika. Ne označava međujezičku pretragu ni višejezičko semantičko razumevanje. Srpski upit po pravilu neće pronaći dokument koji je isključivo na engleskom ako ne dele imena, skraćenice, brojeve, pozajmljene reči ili druge površinske oblike karaktera.

Ovaj metod doprinosi pravednijem poređenju jer se semantički sistemi ne porede samo sa sirovim BM25 rangiranjem reči primenjenim na prirodno-jezička pitanja. Dodata je u literaturi opisana karakterska reprezentacija koja zahteva malo jezičkog predznanja, ali ostaje nesemantička.

## Unapred utvrđen postupak

Postupak je zamrznut pre pregleda publikacije 4349 i novih rangiranja:

1. BM25 komponenta reči;
2. BM25 komponenta karakterskih četvorograma unutar tokena;
3. jednako, nenadgledano spajanje rangova reciprocal-rank fusion postupkom (RRF), uz `k = 60`.

McNamee i Mayfield ispitivali su tokenizaciju karakterskim n-gramima nad kolekcijama za pretragu na evropskim jezicima i koristili četvorograme i petograme. Njihovi rezultati i kasniji CLEF postupak predstavljaju nezavisnu osnovu za jezički neutralnu reprezentaciju četvorogramima. Vrednost četiri izabrana je kao jednostavnija i kraća od te dve potvrđene postavke, a ne prema ocenama relevantnosti iz ove evaluacije. Cormack, Clarke i Büttcher definišu RRF i fiksiraju `k = 60` tokom probnog rada pre naknadne provere. RRF odgovara ovom problemu pošto numerički rezultati indeksa reči i karaktera nisu međusobno kalibrisani. Robertson i Zaragoza daju BM25 okvir i tumačenje njegovih parametara.

Nijedna postojeća ocena relevantnosti nije korišćena za izbor dužine n-grama, pravila spajanja, konstante spajanja, BM25 parametara, težine naslova, normalizacije ili polja. BM25 vrednosti i težina polja preuzete su bez promene iz istorijskog sirovog metoda kako bi se izdvojio uticaj reprezentacije i fiksnog spajanja rangova.

## Tačan postupak analize

Za svaki upit, naslov i sažetak nezavisno se primenjuje sledeće:

1. Nedostajuća vrednost zamenjuje se praznim tekstom.
2. Primenjuje se Unicode Normalization Form KC (NFKC).
3. Velika i mala slova izjednačavaju se podrazumevanim Unicode postupkom kroz Python `str.casefold()`.
4. Kodne tačke čitaju se sleva nadesno. Token reči čini najduži neprekinuti niz čija Unicode opšta kategorija počinje sa `L` (Letter), `M` (Mark) ili `N` (Number). Svaka druga kodna tačka, uključujući beline, interpunkciju, simbole i donju crtu, predstavlja granicu i odbacuje se.
5. Komponenta reči emituje dobijene tokene bez dodatne izmene.
6. Karakterska komponenta emituje svaki preklapajući niz od tačno četiri Unicode kodne tačke unutar tokena. Niz nikada ne prelazi granicu reči i ne dobija posebne oznake granice. Ako token ima manje od četiri kodne tačke, ceo token emituje se jednom kako imena i skraćenice poput `AI` ne bi ostale bez reprezentacije.

Dijakritici se čuvaju. Srpska latinica i srpska ćirilica ostaju različita pisma. Ne primenjuju se svođenje znakova `č/ć/š/ž/đ`, preslovljavanje ćirilice u latinicu ni uklanjanje akcenata. NFKC može da sastavi ili rastavi kompatibilne oblike prema Unicode pravilima, a izjednačavanje slova može promeniti broj kodnih tačaka. Metapodaci beleže verziju Python-a i vrednost `unicodedata.unidata_version` korišćenu pri pravljenju rezultata.

Primeri:

| Ulaz | Tokeni reči | Karakterska reprezentacija (primer) |
|---|---|---|
| `VEŠTAČKA` | `veštačka` | `vešt`, `ešta`, `štač`, `tačk`, `ačka` |
| `ВЕШТАЧКА` | `вештачка` | samo ćirilični četvorogrami |
| `repo-search` | `repo`, `search` | `repo`, `sear`, `earc`, `arch` |
| `AI` | `ai` | `ai` (pravilo za kratke tokene) |

## BM25 bodovanje

Svaka reprezentacija pravi odvojene indekse naslova i sažetka pomoću `bm25s==0.3.10`, metode `lucene` i parametara `k1 = 1.2` i `b = 0.75`. Za izraz upita `t` i polje dokumenta `d`, uobičajeni BM25 oblik je:

\[
BM25(d,q)=\sum_{t\in q} IDF(t)\frac{f(t,d)(k_1+1)}{f(t,d)+k_1\left(1-b+b\frac{|d|}{avgdl}\right)}
\]

gde je:

- `q` analizirani niz izraza upita;
- `f(t,d)` učestalost izraza `t` u polju `d`;
- `|d|` dužina analiziranog polja;
- `avgdl` prosečna analizirana dužina tog polja;
- `k1` parametar zasićenja učestalosti izraza;
- `b` parametar normalizacije dužine;
- `IDF(t)` Lucene težina inverzne učestalosti dokumenta koju daje fiksirana implementacija.

Za svaku komponentu `c` (reč ili karakter), naslov i sažetak boduju se nezavisno i spajaju izrazom:

\[
S_c(D,q)=2.0\,BM25_{c,title}(D,q)+1.0\,BM25_{c,abstract}(D,q).
\]

Težina naslova `2.0`, polja naslova i sažetka i parametri `k1` i `b` preuzeti su iz istorijske sirove BM25 evaluacije i nisu ponovo birani. Nedostajući ili prazan sažetak daje praznu reprezentaciju polja i doprinos nula.

Unutar jedne komponente dokumenti sa pozitivnim rezultatom poređani su prema opadajućoj vrednosti `S_c`, a izjednačeni rezultati prema rastućem tekstualnom `publication_id`. Dokument čiji je rezultat komponente nula ne ulazi u njeno rangiranje.

## Reciprocal-rank fusion

Neka je `R = {word, char4}`, a `r_c(D)` pozicija dokumenta `D`, počev od jedan, u komponenti `c`. Spojeni rezultat je:

\[
RRF(D)=\sum_{c\in R:D\in c}\frac{1}{60+r_c(D)}.
\]

Konstanta je fiksirana na `60`, a komponente imaju jednake težine. Dokument koji ne postoji u jednoj komponenti dobija nulti doprinos te komponente. Spajanje razmatra svaki dokument sa pozitivnim rezultatom, a ne samo traženu dubinu izlaza. Završni rezultati poređani su prema opadajućem RRF rezultatu, zatim prema rastućem tekstualnom `publication_id`. Time se dobijaju deterministički identiteti i rezultati, dok trajanje i vreme pravljenja prirodno zavise od konkretnog izvršavanja.

## Pseudokod

```text
build(corpus):
  for field in [title, abstract]:
    word_docs[field]  = analyze_words(corpus[field])
    char4_docs[field] = analyze_char4(corpus[field])
    word_index[field]  = BM25(word_docs[field], k1=1.2, b=0.75)
    char4_index[field] = BM25(char4_docs[field], k1=1.2, b=0.75)

retrieve(query, limit):
  word_scores  = 2 * word_title(query)  + word_abstract(query)
  char4_scores = 2 * char4_title(query) + char4_abstract(query)
  word_rank  = rank_positive(word_scores,  score desc, publication_id asc)
  char4_rank = rank_positive(char4_scores, score desc, publication_id asc)
  for each document in union(word_rank, char4_rank):
    score = (1/(60 + word_rank[document]) if present else 0)
          + (1/(60 + char4_rank[document]) if present else 0)
  return first limit documents by score desc, publication_id asc
```

## Zamrznuti postupak evaluacije i poreklo podataka

Generator `python -m evaluation.language_independent_lexical_artifacts` zahteva pune očekivane SHA-256 vrednosti skupa upita, snimka korpusa i istorijske datoteke izvršavanja. Odbija neusaglašene vrednosti i ne prepisuje postojeći direktorijum. Lokalno pokreće samo `language_independent_lexical`, učitava zamrznutu istorijsku datoteku i kopira zapise `vector_only` i `full_pipeline` bez pozivanja njihovih embedding ili servisnih granica. Ponovo korišćeni zapisi proveravaju se prema zamrznutom korpusu, a hash vrednosti kanonskih zapisa porede se i posle upisa.

Generisani metapodaci obuhvataju verzije metoda i analizatora, verzije Python-a, Unicode-a i biblioteke bm25s, svaku operaciju normalizacije, opseg n-grama, pravila za kratke tokene i granice, BM25 parametre i parametre polja, RRF pravilo, obradu izjednačenih rezultata, logičku statistiku indeksa, izvorni i početni commit, hash vrednosti korpusa, upita i zamrznutih izvršavanja, hash vrednosti ponovo korišćenih zapisa po metodu i dobijenih datoteka, broj dokumenata i upita, top-k, dubinu skupa, seed, trajanje i UTC vreme. Logička veličina indeksa prikazuje se kao broj pojavljivanja izraza analizatora, veličina rečnika i broj UTF-8 bajtova rečnika; ne naziva se veličinom serijalizovanog indeksa ni rezidentne memorije.

Skup kandidata predstavlja uniju prvih pet rezultata metoda `language_independent_lexical`, `vector_only` i `full_pipeline` za svaki od 30 zamrznutih upita. Ponovljeni parovi `(query_id, publication_id)` spajaju se u jedan. Redosled kandidata meša se ponovljivo unutar svakog upita uz seed `2026`. Identitet metoda, pozicija i rezultat nisu prikazani u listu za ocenjivanje.

Završene ocene prenose se isključivo prema tačnom paru `(query_id, publication_id)`. Prazna polja ostaju prazna, a program broji neslaganja, ponovljene parove, nevažeće ocene, prethodno ocenjene parove koji su napustili skup i nove neocenjene parove. Relevantnost se ne izvodi automatski. Završno računanje Precision@5, nDCG@5 i MRR@5 odlaže se dok svaki novi par ne dobije ručnu ocenu.

Probne datoteke i radna sveska za ocenjivanje čuvaju se lokalno i isključeni su iz repozitorijuma. Praćeni opis postupka i izvorni commit čine ponovljivi zapis implementacije.

### Zamrznuto probno izvršavanje od 9. avgusta 2026.

Izolovano probno izvršavanje napravljeno je iz izvornog commita `9c7208e42a12e5a2da65eeee2802f51f5616a1c6`, koji neposredno potiče od zabeleženog početnog `test` commita `bb88a7bdfc65139ba2465cbc47f2347257b89001`.

Hash vrednosti zaštićenih ulaza odgovarale su pre izrade i posle provere radne sveske:

- upiti: `8fe5748b24f16f6c9e2d3037002eab1d4a613df1e1d419827da3768961d03f88`;
- snimak korpusa: `b366854b50c7abb40b51c29a943f89fdd22b0af33cac6b6cd3371ff2404eebce`;
- ulaz istorijskih izvršavanja: `86b36e45e377d42a07407150de14f309c4383f012466e22e5e3ae6d2db07264e`;
- izvorna radna sveska: `0fff8874465fced16a8b4b2581884613eb4fb2846ce7c55ae932f42451a8381d`.

Hash vrednosti dobijenih datoteka:

- `language-independent-lexical-runs.json`: `e89a820142acb94e9e8a4a6e394ee670e2e535285ea47b13a58e7318078b2430`;
- objedinjeni `runs.json`: `0240547bd1b9ab085ab26461d2b3fa8a09df2f6a5b750404864bcf8a35c24011`;
- zaslepljeni `candidates.csv`: `51b5f4765e1b2aa36b6d396d41720f969d4f1aa9a758d53c1ff40c7e6fec7dad`;
- `metadata.json`: `d77b8004c97a1520fb8d4fa59599d68cbfd6ad7ada5c6116a51b302676398273`;
- `comparison/lexical-comparison.json`: `451c48dc61297a900328fe0f806f2fec517c4c34eecb6936e1af2a1bcd325ea3`;
- proširena radna sveska: `b9fc752a571132ca5cf3b6ca50ac9b6a24a447d200414089b8a21eb192af8714`;
- završni izveštaj o prenosu ocena: `419e8ac571ddeb01e102a802ccafa9b55ba3f73590d99bee83cee295925b8c75`.

Skup sadrži 390 parova. U odnosu na istorijski skup sirovog BM25 metoda, 341 par ostaje, 49 izlazi i 49 ulazi. Od 253 završene ocene, 225 se prenosi tačno, 28 ocenjenih parova napušta skup, stara radna sveska ima 137 praznih redova, a nova 165 neocenjenih redova. Broj neslaganja, grešaka ponovljenih parova i nevažećih ocena je nula. Reč je o pokazateljima obima rada i porekla podataka, a ne o rezultatima uspešnosti.

Lokalno sačuvana i proverena radna sveska sadrži tri prikazana i ponovo otvorena lista, četiri duga sažetka proširena u posebnom listu sa celim tekstom i nijednu uočenu grešku formule.

Poređenje koje ne koristi ocene relevantnosti zadržava 96 od 150 prvih pet parova sirovog leksičkog metoda i 341 od 390 parova celog skupa. Publikacija 4349 pregledana je tek pošto su metod i izvršavanje zamrznuti: za upite q17, q19 i q20 nalazi se na pozicijama 17, 8 i 15. Ovo je naknadna analiza greške, a ne razlog za izbor metoda.

## Testovi

Automatizovani testovi obuhvataju deterministički redosled i rezultate; NFKC i svođenje slova; srpsku ćirilicu i latinicu; engleski i mešoviti tekst; očuvane dijakritike i kombinovane znakove; granice interpunkcije; kratke tokene, imena i skraćenice; prazne ili nedostajuće sažetke; tačne metapodatke; nevažeće vrednosti n-grama, RRF-a i komandne linije; odbijanje pogrešnih zamrznutih hash vrednosti; odsustvo poziva semantičke pretrage tokom izolovane izrade; nepromenjene ponovo korišćene zapise vektorske i kompletne putanje; zaslepljeno formiranje skupa bez duplikata; i prenos ocena za stabilne parove sa brojem praznih, konfliktnih i neuparenih stavki. Sintetički ulazi ne sadrže zamrznute ljudske ocene relevantnosti.

## Grupe evaluacije

Metapodaci upita moraju i dalje sadržati jezik, pismo, kategoriju i temu. Analiza u poglavlju 6 treba da razlikuje najmanje leksičke potrebe na istom jeziku od međujezičkih informacionih potreba. Leksička osnova ima najviše smisla u grupi istog jezika i zajedničkih oblika. Ne sme se predstavljati kao dokaz da karakterski n-grami rešavaju međujezičku pretragu.

## Ograničenja i odbranjive tvrdnje

Moguće je tvrditi:

- jedan fiksirani analizator obrađuje površinski tekst srpske ćirilice, srpske latinice i engleskog jezika u ovom korpusu;
- karakterski četvorogrami smanjuju zavisnost od potpuno jednakih celih reči i mogu deliti signal između srodnih flektivnih i pravopisnih oblika unutar istog pisma;
- metod je deterministički, jasan, ponovljiv i strogo leksički;
- metod je prikladniji za poređenje od korišćenja samo nepromenjenih tokena reči iz prirodno-jezičkih pitanja, bez tvrdnje o uspešnosti pre završetka ocenjivanja.

Nije moguće tvrditi:

- univerzalnu višejezičku podršku;
- semantičko razumevanje, sinonimiju ili pojmovnu jednakost;
- pretragu sa srpskog na engleski ili sa ćirilice na latinicu bez zajedničkih oblika;
- otpornost na proizvoljne pravopisne razlike, OCR greške ili preslovljavanje;
- nadmoć nad sirovim BM25 metodom pre kompletnih ocena i unapred definisanih mera;
- reprodukciju Google Scholar, DSpace/Solr ili drugog produkcionog indeksa repozitorijuma.

Karakterski indeksi veći su od indeksa reči i mogu odgovarati slučajnim delovima teksta. RRF odbacuje veličinu rezultata i jednako tretira obe komponente. NFKC i svođenje slova nisu povratne operacije. Interpunkcija se odbacuje, pa izrazi sa mnogo simbola, kao `C++`, gube razlike. Pravilo za kratke tokene čuva odziv skraćenica, ali može zadržati i opšte kratke oblike.

## Okvir metodologije za master rad

1. Obrazložiti izbor pravednog, nesemantičkog metoda za poređenje prirodno-jezičkih upita na srpskom i engleskom.
2. Definisati „jezički nezavisan” kao ponovnu upotrebu analizatora, a ne međujezičko razumevanje.
3. Navesti NFKC, svođenje slova, granice tokena prema Unicode kategorijama, očuvanje dijakritika i pisama, tačne četvorograme i pravilo za kratke tokene.
4. Prikazati odvojene BM25 izraze naslova i sažetka i preuzeti odnos polja `2:1`.
5. Prikazati RRF sa dve jednake komponente i fiksnim `k=60`.
6. Navesti determinističko rešavanje jednakih rezultata i podatke potrebne za ponavljanje postupka.
7. Objasniti zamrznute hash vrednosti, ponovnu upotrebu vektorskih i kompletnih izvršavanja, zaslepljeno formiranje skupa dubine pet i prenos ocena prema stabilnom paru.
8. Prikazati promene skupa, trajanje i logičku veličinu indeksa bez korišćenja nepotpunih ocena za tvrdnju o poboljšanju.
9. Posle završetka ocenjivanja analizirati rezultate prema jeziku, pismu i vrsti informacione potrebe.
10. Ograničiti zaključke na leksičko preklapanje i korišćeni zamrznuti korpus i skup upita.

## Literatura za proveru u Zotero biblioteci i radu

Sledeći izvori uvedeni su uz ovaj metod i treba ih proveriti pre dodavanja u Zotero ili citiranja u radu. Tekst iz izvora nije prepisivan u ovaj dokument.

1. Paul McNamee and James Mayfield, “Character N-Gram Tokenization for European Language Text Retrieval,” *Information Retrieval* 7, 73–97 (2004). DOI: `10.1023/B:INRT.0000009441.78971.be`. Stable URL: https://doi.org/10.1023/B:INRT.0000009441.78971.be. Izvor podržava karakterske n-grame kao reprezentaciju koja zahteva malo jezičkog predznanja i eksperimentalnu primenu četvorograma i petograma; ne podržava tvrdnju o semantičkoj ili univerzalnoj višejezičkoj pretrazi.
2. Gordon V. Cormack, Charles L. A. Clarke, and Stefan Büttcher, “Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods,” SIGIR 2009. DOI: `10.1145/1571941.1572114`; ISBN: `978-1-60558-483-6`. Author PDF: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf. Izvor podržava RRF izraz, obrazloženje spajanja samo prema rangu i fiksno `k=60`.
3. Stephen Robertson and Hugo Zaragoza, “The Probabilistic Relevance Framework: BM25 and Beyond,” *Foundations and Trends in Information Retrieval* 3(4), 333–389 (2009). DOI: `10.1561/1500000019`; ISBN: `978-1-60198-308-4`. Stable URL: https://doi.org/10.1561/1500000019. Izvor podržava BM25 okvir, zasićenje učestalosti izraza, normalizaciju prema dužini dokumenta i tumačenje parametara.
4. Mark Davis and Martin Dürst, “Unicode Normalization Forms,” Unicode Standard Annex #15. Stable URL: https://unicode.org/reports/tr15/. No DOI/ISBN. Autoritativni izvor za NFKC; konkretna verzija Unicode podataka ostaje deo podataka o izvršavanju.
5. The Unicode Consortium, *The Unicode Standard*, default case operations and CaseFolding data. Stable URLs: https://www.unicode.org/versions/latest/ and https://www.unicode.org/Public/UCD/latest/ucd/CaseFolding.txt. ISBN for the continuously updated online standard is not assigned. Izvor podržava operaciju izjednačavanja velikih i malih slova i terminologiju opštih Unicode kategorija.

Poreklo same implementacije, koje obično nije bibliografski izvor master rada: `bm25s==0.3.10`, https://github.com/xhluca/bm25s, Apache-2.0. Biblioteka je već bila fiksirana zavisnost; ova promena ne uvodi novu biblioteku.
