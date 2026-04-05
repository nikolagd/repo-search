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


Pokretanje iz komandne linije sa:
```bash
python -m etl.main
```

Grube provere da li je upisivanje u bazu uspelo:
```bash
psql -U postgres -d [naziv_baze] -p [port] -f etl/checks.sql
```
