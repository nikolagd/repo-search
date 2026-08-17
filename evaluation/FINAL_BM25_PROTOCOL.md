# Završni protokol za BM25 poređenje

> Istorijski protokol: ova datoteka čuva prvobitni plan evaluacije sirovog BM25 metoda. Njega je zamenio protokol `LANGUAGE_INDEPENDENT_LEXICAL_BASELINE.md`, čiji je identifikator metoda `language_independent_lexical`. Sirovi `bm25` ostaje dostupan radi ponavljanja ranijeg postupka, ali se više ne koristi kao leksički metod pri formiranju skupa kandidata.

Završno poređenje za master rad koristi svih 30 zamrznutih upita i tačno tri metoda: `bm25`, `vector_only` i `full_pipeline`. Skup kandidata predstavlja uniju prvih pet rezultata svakog metoda za svaki upit, pri čemu je identitet metoda skriven, parovi se uklanjaju prema ključu `(query_id, publication_id)`, a redosled se meša uz seed `2026`. Svaki par u skupu mora dobiti ručnu ocenu relevantnosti 0, 1 ili 2 pre računanja završnih mera uspešnosti.

Osnovne mere relevantnosti su **Precision@5**, **nDCG@5** i **MRR@5**. Dubina formiranog skupa omogućava zaključke samo do pete pozicije. Recall, Recall@10, nDCG@10, neograničeni MRR i kvalitet rezultata ispod pete pozicije ne smeju se izvoditi iz ovog skupa. Opšti program za evaluaciju može da izračuna i ta polja radi ponovne upotrebe, ali ona nisu deo ovog protokola i ne koriste se u završnom poređenju.

Dubina pet izabrana je kao metodološki minimum koji obuhvata jednu korisnu stranicu rezultata za svaki metod, a da ručno ocenjivanje ostane izvodljivo. Dubine tri i četiri ne obuhvataju celu takvu stranicu, dok dubina deset znatno povećava broj parova za procenu. Lokalni, ignorisani izveštaj čuva izmereno poređenje veličine skupova za dubine 3, 4, 5 i 10.

`bm25` je završni leksički metod za poređenje: ponovljiva osnova u Lucene stilu nad zamrznutim lokalnim korpusom, sa fiksiranom verzijom `bm25s==0.3.10`, parametrima `k1=1.2` i `b=0.75`, Unicode NFKC normalizacijom i svođenjem velikih i malih slova, Unicode tokenima reči, odvojenim indeksima naslova i sažetka, bez uklanjanja stop reči i sa rezultatom `2.0 * title BM25 + abstract BM25`. Ovaj metod nije reprodukcija Google Scholar pretrage niti se tvrdi da je jednak DSpace/Solr konfiguraciji bilo kog izvornog repozitorijuma.

`vector_only` predstavlja semantičku pretragu bez tumačenja upita. `full_pipeline` predstavlja potpunu putanju aplikacione pretrage sa postojećim ponašanjem režima parsera. Raniji metod `keyword`, zasnovan na učestalosti tokena, ostaje dostupan samo radi kompatibilnosti sa istorijskim postupkom i nije deo ovog protokola.
