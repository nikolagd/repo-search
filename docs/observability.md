# Observability

Ovaj sloj počinje metrikama performansi mikroservisa i lokalnim Grafana stack-om.

## Šta je uključeno

- FastAPI `/metrics` endpoint-i za gateway, auth, catalog, search, query, embedding i job servise.
- Metrics endpoint za worker na `job-worker:9100`.
- Prometheus scrape za aplikacione, worker, Postgres i kontejnerske metrike.
- Grafana datasource i početni dashboard za request rate, p95 latenciju, latenciju zavisnosti, greške, embedding, dubinu job reda, CPU i memoriju.
  Paneli `Request Rate`, `Inbound Latency p95` i `5xx Error Rate` filtriraju `/live`, `/ready`, `/health` i odgovarajuće gateway `/api/*` rute, kako bi prikazivali realan korisnički/API saobraćaj, a ne probe orkestratora.
- Postgres exporter za zdravlje i opterećenje baze.
- kube-state-metrics za stanje Kubernetes objekata: podovi, deployment-i, restarti i replike.
- node-exporter za CPU i memoriju Kubernetes node-a.
- DCGM exporter u `k8s-gpu` overlay-u za NVIDIA GPU iskorišćenost i memoriju.
- OpenTelemetry Collector za prijem trace podataka iz mikroservisa.
- Jaeger za pregled distributed tracing-a kroz jedan konkretan request ili job.
- cAdvisor za kontejnerske resource metrike u Docker Compose-u.

## Pokretanje preko Docker Compose-a

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml up --build -d
```

Otvoriti:

- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090
- cAdvisor, ako je uključen opcioni profil za kontejnerske metrike: http://localhost:8088

Podrazumevani Grafana login dolazi iz `.env.microservices`:

```text
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin
```

Promeniti lozinku pre korišćenja van lokalnog development okruženja.

CPU/memory metrike kontejnera preko cAdvisor-a su opcione zato što Docker Desktop na Windows-u može biti osetljiv na Linux host bind mount-ove. Uključiti po potrebi:

```powershell
docker compose --env-file .env.microservices -f docker-compose.microservices.yml --profile container-metrics up -d cadvisor
```

## Metrike koje prvo treba gledati

- `repo_search_http_requests_total`
- `repo_search_http_request_duration_seconds`
- `repo_search_outbound_http_request_duration_seconds`
- `repo_search_search_request_duration_seconds`
- `repo_search_query_parse_duration_seconds`
- `repo_search_embedding_duration_seconds`
- `repo_search_job_queue_depth`
- `repo_search_jobs_by_status`
- `repo_search_job_oldest_queued_age_seconds`
- `repo_search_job_oldest_running_age_seconds`
- `repo_search_job_duration_seconds`
- `kube_pod_container_status_restarts_total`
- `kube_deployment_status_replicas_unavailable`
- `node_cpu_seconds_total`
- `node_memory_MemAvailable_bytes`
- `DCGM_FI_DEV_GPU_UTIL`
- `DCGM_FI_DEV_FB_USED`

Korisni PromQL primeri:

```promql
sum by (service) (rate(repo_search_http_requests_total[5m]))
```

```promql
histogram_quantile(0.95, sum by (le, service) (rate(repo_search_http_request_duration_seconds_bucket[5m])))
```

```promql
histogram_quantile(0.95, sum by (le, service, upstream_service) (rate(repo_search_outbound_http_request_duration_seconds_bucket[5m])))
```

```promql
sum by (service, status_code) (rate(repo_search_http_requests_total{status_code=~"5.."}[5m]))
```

Aktivni poslovi u redu ili obradi:

```promql
repo_search_jobs_by_status{service="job-service",status=~"queued|running"}
```

Nedavni job događaji, za proveru šta se desilo u poslednjem satu:

```promql
sum by (job_type, status) (increase(repo_search_job_events_total{service="job-worker",job_type!="worker"}[1h]))
```

```promql
sum by (pod) (increase(kube_pod_container_status_restarts_total{namespace="repo-search"}[1h]))
```

```promql
avg by (gpu, UUID) (DCGM_FI_DEV_GPU_UTIL)
```

## Distributed tracing

Prometheus i Grafana pokazuju agregirane metrike kroz vreme: koliko ima request-a, kolika je p95 latencija, koliko ima grešaka i koliko su opterećeni podovi, baza i GPU.

OpenTelemetry i Jaeger dodaju drugi pogled: putanju jednog konkretnog request-a ili jednog konkretnog background job-a kroz mikroservise. To je korisno kada treba objasniti gde se potrošilo vreme u jednom pozivu.

Primer trace-a za pretragu:

```text
gateway
  search-service
    query-service /query/parse
      llm.parse_query
      llm.call
    embedding-service /embed/query
      embedding.generate
    search.vector_db
```

Primer trace-a za harvest ili embedding backfill:

```text
job-worker
  job.run
    job.harvest_repository ili job.backfill_embeddings
      catalog-service
      embedding-service
      search-service
```

Tracing je uključen u Kubernetes-u kroz `repo-search-config`:

```yaml
OTEL_TRACING_ENABLED: "true"
OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
OTEL_SERVICE_NAMESPACE: repo-search
OTEL_DEPLOYMENT_ENVIRONMENT: local-kubernetes
```

Python servisi inicijalizuju tracing u `microservices/common/observability.py`. Isti modul uključuje FastAPI instrumentation, HTTPX/Requests instrumentation, psycopg2 instrumentation i ručno dodate span-ove za search, query parse, embedding i job tokove.

## Kubernetes metrics-server

`metrics-server` treba da bude deo Kubernetes sloja, ali nije zamena za Prometheus.

Koristi se za:

- `kubectl top nodes`
- `kubectl top pods`
- HorizontalPodAutoscaler CPU/memory signale

Ne obezbeđuje:

- request latenciju
- throughput po ruti
- service-to-service latenciju
- job metrike
- embedding/search/query merenja
- dugoročno čuvanje metrika
- Grafana dashboard-e sam po sebi

Za Minikube:

```powershell
minikube addons enable metrics-server
kubectl top pods -n repo-search
```

Za standardni klaster, instalirati Kubernetes metrics-server release koji odgovara pravilima klastera, zatim proveriti:

```powershell
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl top nodes
```

Sledeći Kubernetes observability korak je dodavanje Prometheus scrape-a unutar klastera, kroz Prometheus Operator/ServiceMonitor resurse ili običnu Prometheus scrape konfiguraciju. `metrics-server` ostaje koristan za HPA i brze provere resource usage-a, dok Prometheus ostaje izvor za dashboard-e performansi aplikacije.

## Pokretanje u Kubernetes-u

Observability manifesti su uključeni u bazu, a primarni GPU deployment ih proširuje DCGM exporter-om i Prometheus GPU scrape konfiguracijom:

```powershell
kubectl apply -k k8s-gpu/
```

`k8s-gpu/` već uključuje `k8s/`; ne treba primenjivati oba direktorijuma redom. Eksplicitni CPU fallback za razvoj koristi `kubectl apply -k k8s/` i tada nema DCGM exporter.

Sačekati rollout:

```powershell
kubectl -n repo-search rollout status deployment/prometheus --timeout=180s
kubectl -n repo-search rollout status deployment/grafana --timeout=180s
kubectl -n repo-search rollout status deployment/postgres-exporter --timeout=180s
kubectl -n repo-search rollout status deployment/otel-collector --timeout=180s
kubectl -n repo-search rollout status deployment/jaeger --timeout=180s
```

Otvoriti Prometheus:

```powershell
kubectl -n repo-search port-forward service/prometheus 9090:9090
```

Zatim otvoriti:

```text
http://localhost:9090
```

Otvoriti Grafana:

```powershell
kubectl -n repo-search port-forward service/grafana 3000:3000
```

Zatim otvoriti:

```text
http://localhost:3000
```

Podrazumevani login je:

```text
admin / admin
```

Proveriti Prometheus targets:

```text
http://localhost:9090/targets
```

Otvoriti Jaeger:

```powershell
kubectl -n repo-search port-forward service/jaeger 16686:16686
```

Zatim otvoriti:

```text
http://localhost:16686
```

U Jaeger UI-u izabrati servis, na primer `gateway`, `search-service`, `query-service`, `embedding-service` ili `job-worker`, pokrenuti pretragu ili job u aplikaciji i zatim kliknuti `Find Traces`.
