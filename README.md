Primarni način pokretanja aplikacije je Kubernetes preko lokalnog Minikube klastera.

Docker Compose uputstvo je arhivirano u [docs/docker-compose-microservices.md](docs/docker-compose-microservices.md) i treba ga koristiti samo za lokalni smoke test, debugging van Kubernetes-a ili poređenje sa manifestima.

## 1. Preduslovi

Potrebno je:

- Docker Desktop
- Minikube
- kubectl
- NVIDIA driver ako se koristi GPU

Instalacija Minikube-a i kubectl-a:

```powershell
winget install Kubernetes.minikube
winget install Kubernetes.kubectl
```

Pokrenuti Docker Desktop pre pokretanja Minikube-a.

Provera GPU-a:

```powershell
nvidia-smi
```

## 2. Pokretanje Minikube klastera

CPU verzija:

```powershell
minikube start --driver=docker --profile repo-search
kubectl config use-context repo-search
```

GPU verzija:

U Docker Desktop-u proveriti da je NVIDIA runtime dostupan i postavljen kao default runtime:

```text
Docker Desktop -> Settings -> Docker Engine
```

U JSON konfiguraciji dodati ili proveriti ovaj deo:

```json
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
```

Sačuvati promenu i restartovati Docker Desktop. Zatim napraviti novi Minikube klaster sa GPU podrškom:

```powershell
minikube delete --profile repo-search
minikube start --driver=docker --container-runtime=docker --gpus=all --cni=cilium --profile repo-search --cpus=4 --memory=12000 --disk-size=20000mb
kubectl config use-context repo-search
minikube addons enable nvidia-device-plugin -p repo-search
kubectl -n kube-system rollout status daemonset/nvidia-device-plugin-daemonset --timeout=180s
```

Provera da li Kubernetes vidi GPU:

```powershell
kubectl describe node repo-search | Select-String nvidia.com/gpu
```

## 3. Metrics server

`metrics-server` je Kubernetes sloj za CPU/memory metrike, `kubectl top` i kasnije HPA pravila.

```powershell
minikube addons enable metrics-server -p repo-search
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
kubectl top nodes
```

Prometheus, Grafana, kube-state-metrics, node-exporter i Postgres exporter su deo Kubernetes manifesta u `k8s/05-observability.yaml`.
OpenTelemetry Collector i Jaeger su deo `k8s/06-tracing.yaml`.
GPU overlay `k8s-gpu/` dodaje DCGM exporter za NVIDIA GPU metrike.

## 4. Build image-a unutar Minikube-a

Usmeriti PowerShell na Minikube Docker daemon:

```powershell
& minikube -p repo-search docker-env --shell powershell | Invoke-Expression
```

Build backend image-a:

```powershell
docker build -f Dockerfile.microservice `
  --build-arg PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu130 `
  -t repo-search-microservices-auth-service:latest `
  -t repo-search-microservices-catalog-service:latest `
  -t repo-search-microservices-search-service:latest `
  -t repo-search-microservices-query-service:latest `
  -t repo-search-microservices-embedding-service:latest `
  -t repo-search-microservices-job-service:latest `
  -t repo-search-microservices-gateway:latest `
  .
```

Build frontend image-a:

```powershell
docker build -f frontend/Dockerfile -t repo-search-microservices-frontend:latest .
```

## 5. Deploy

CPU deploy:

```powershell
kubectl apply -k k8s/
```

GPU deploy:

```powershell
kubectl apply -k k8s-gpu/
```

GPU overlay dodeljuje `nvidia.com/gpu` resurs za `embedding-service` i uključuje NVIDIA runtime za `ollama`.

Sačekati rollout:

```powershell
kubectl -n repo-search rollout status statefulset/db-primary --timeout=180s
kubectl -n repo-search rollout status statefulset/db-replica --timeout=180s
kubectl -n repo-search rollout status deployment/auth-service --timeout=300s
kubectl -n repo-search rollout status deployment/catalog-service --timeout=300s
kubectl -n repo-search rollout status deployment/query-service --timeout=300s
kubectl -n repo-search rollout status deployment/embedding-service --timeout=300s
kubectl -n repo-search rollout status deployment/search-service --timeout=300s
kubectl -n repo-search rollout status deployment/job-service --timeout=300s
kubectl -n repo-search rollout status deployment/gateway --timeout=300s
kubectl -n repo-search rollout status deployment/frontend --timeout=300s
kubectl -n repo-search rollout status deployment/ollama --timeout=300s
kubectl -n repo-search rollout status deployment/job-worker --timeout=300s
kubectl -n repo-search rollout status deployment/prometheus --timeout=180s
kubectl -n repo-search rollout status deployment/grafana --timeout=180s
kubectl -n repo-search rollout status deployment/postgres-exporter --timeout=180s
kubectl -n repo-search rollout status deployment/kube-state-metrics --timeout=180s
kubectl -n repo-search rollout status daemonset/node-exporter --timeout=180s
kubectl -n repo-search rollout status deployment/otel-collector --timeout=180s
kubectl -n repo-search rollout status deployment/jaeger --timeout=180s
kubectl -n repo-search rollout status daemonset/dcgm-exporter --timeout=180s
```

Proveriti podove:

```powershell
kubectl -n repo-search get pods
```

Ako su image-i rebuildovani sa istim `:latest` tagovima, restartovati deployment-e:

```powershell
kubectl -n repo-search rollout restart deployment/auth-service
kubectl -n repo-search rollout restart deployment/catalog-service
kubectl -n repo-search rollout restart deployment/query-service
kubectl -n repo-search rollout restart deployment/embedding-service
kubectl -n repo-search rollout restart deployment/search-service
kubectl -n repo-search rollout restart deployment/job-service
kubectl -n repo-search rollout restart deployment/gateway
kubectl -n repo-search rollout restart deployment/frontend
kubectl -n repo-search rollout restart deployment/ollama
kubectl -n repo-search rollout restart deployment/job-worker
```

## 6. Ollama model

Proveriti modele:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama list
```

Povući model ako nije već prisutan:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama pull gemma4:12b
```

Zagrejati model pre prve pretrage:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama run gemma4:12b "Return only: ok"
kubectl -n repo-search rollout restart deployment/query-service
kubectl -n repo-search rollout status deployment/query-service --timeout=300s
```

`query-service` radi LLM warm-up na startup-u kada je `LLM_WARMUP_ENABLED=1`.

Ako Ollama vrati da model zahteva noviju verziju, primeniti manifest i restartovati Ollama pod da Kubernetes povuce novi `ollama/ollama:latest` image:

```powershell
kubectl apply -k k8s-gpu/
kubectl -n repo-search rollout restart deployment/ollama
kubectl -n repo-search rollout status deployment/ollama --timeout=300s
kubectl -n repo-search exec deployment/ollama -- ollama --version
```

## 7. Otvaranje aplikacije

Pokrenuti Minikube service tunnel:

```powershell
minikube service frontend -n repo-search -p repo-search
```

Na Windows-u obično radi adresa koju komanda prikaže sa `127.0.0.1`.

Može se proveriti i NodePort adresa:

```powershell
minikube ip -p repo-search
```

Otvoriti:

```text
http://<minikube-ip>:30091
```

Gateway health check:

```powershell
kubectl -n repo-search port-forward service/gateway 8090:8000
curl.exe -H "X-API-Key: replace_with_a_long_random_local_token" http://localhost:8090/api/health
```

## 8. Observability

Prometheus, Grafana, Postgres exporter, kube-state-metrics i node-exporter se deploy-uju kroz `k8s/05-observability.yaml`.
OpenTelemetry Collector i Jaeger se deploy-uju kroz `k8s/06-tracing.yaml`.
GPU deploy kroz `k8s-gpu/` dodaje i DCGM exporter.

Port-forward procesi se ne čuvaju posle restartovanja terminala, Docker Desktop-a ili Minikube klastera. Posle novog pokretanja klastera treba ih ponovo startovati. Najpraktičnije je otvoriti poseban PowerShell prozor za svaki od ovih procesa:

```powershell
kubectl -n repo-search port-forward service/gateway 8090:8000
kubectl -n repo-search port-forward service/prometheus 9090:9090
kubectl -n repo-search port-forward service/grafana 3000:3000
kubectl -n repo-search port-forward service/jaeger 16686:16686
```

Dok je komanda aktivna, odgovarajući lokalni URL radi. Kada se prozor zatvori ili se proces prekine sa `Ctrl+C`, port-forward više nije aktivan.

Otvaranje Prometheus-a:

```powershell
kubectl -n repo-search port-forward service/prometheus 9090:9090
```

Zatim otvoriti:

```text
http://localhost:9090
```

Otvaranje Grafana-e:

```powershell
kubectl -n repo-search port-forward service/grafana 3000:3000
```

Zatim otvoriti:

```text
http://localhost:3000
```

Podrazumevani Grafana login:

```text
admin / admin
```

Proveriti Prometheus targets:

```text
http://localhost:9090/targets
```

Otvaranje Jaeger UI-a za distributed tracing:

```powershell
kubectl -n repo-search port-forward service/jaeger 16686:16686
```

Zatim otvoriti:

```text
http://localhost:16686
```

U Jaeger-u se mogu birati servisi kao `gateway`, `search-service`, `query-service`, `embedding-service` i `job-worker`.
Prometheus/Grafana prikazuju agregirane metrike kroz vreme, dok Jaeger prikazuje putanju jednog konkretnog request-a ili job-a kroz mikroservise.

Detalji su u [docs/observability.md](docs/observability.md).

## 9. Korisne komande

Lista podova:

```powershell
kubectl -n repo-search get pods
```

Lista servisa:

```powershell
kubectl -n repo-search get services
```

Logovi:

```powershell
kubectl -n repo-search logs deployment/gateway -f
kubectl -n repo-search logs deployment/search-service -f
kubectl -n repo-search logs deployment/embedding-service -f
kubectl -n repo-search logs deployment/job-worker -f
kubectl -n repo-search logs deployment/prometheus -f
kubectl -n repo-search logs deployment/grafana -f
kubectl -n repo-search logs deployment/otel-collector -f
kubectl -n repo-search logs deployment/jaeger -f
```

Provera GPU-a u embedding podu:

```powershell
kubectl -n repo-search exec deployment/embedding-service -- python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

Provera Kubernetes resource metrika:

```powershell
kubectl top pods -n repo-search
kubectl top nodes
```

## 10. Troubleshooting

Ako podovi nisu spremni:

```powershell
kubectl -n repo-search get pods
kubectl -n repo-search describe pod <ime-poda>
```

Ako servis pada ili je u `CrashLoopBackOff` stanju:

```powershell
kubectl -n repo-search logs <ime-poda> --previous
kubectl -n repo-search logs <ime-poda>
```

Ako frontend URL sa Minikube IP adresom ne radi na Windows-u:

```powershell
minikube service frontend -n repo-search -p repo-search
```

Ako `kubectl port-forward` vrati grešku kao:

```text
Unable to connect to the server: dial tcp 127.0.0.1:<port>: connectex: No connection could be made because the target machine actively refused it.
```

problem nije u servisu koji se forward-uje, već u tome što `kubectl` ne može da dođe do Minikube API servera. To se često desi posle restartovanja Docker Desktop-a, kada Minikube profil ostane upisan u kubeconfig-u sa starim lokalnim portom.

Proveriti stanje:

```powershell
minikube status -p repo-search
kubectl config current-context
```

Ako status kaže `apiserver: Stopped`, `kubelet: Stopped` ili `kubeconfig: Misconfigured`, pokrenuti:

```powershell
minikube start -p repo-search
kubectl config use-context repo-search
kubectl -n repo-search get pods
```

Ako Minikube posebno prijavi stale context, može se ručno osvežiti:

```powershell
minikube update-context -p repo-search
```

Sačekati da podovi pređu u `1/1 Running`, pa ponovo pokrenuti port-forward komande iz sekcije Observability.

Ako posle restartovanja Docker Desktop-a ili `minikube start` podovi prvo izgledaju kao da su puni grešaka, ne restartovati odmah sve ponovo. Često je u pitanju normalan redosled oporavka:

```text
embedding-service -> search-service -> gateway
```

`embedding-service` mora prvo da dobije GPU, učita model i prođe readiness proveru. Dok se to ne desi, `search-service` može ostati u `Init` stanju jer čeka `embedding-service`, a `gateway` može biti `0/1` jer njegov health check poziva downstream servise. Proveriti stanje nekoliko puta u razmaku od 30-60 sekundi:

```powershell
kubectl -n repo-search get pods
kubectl -n repo-search get endpoints embedding-service search-service gateway
```

Ako je novi pod istog deployment-a zdrav, a stari pod i dalje stoji u `Error` ili `UnexpectedAdmissionError`, taj stari pod se može obrisati:

```powershell
kubectl -n repo-search delete pod <stari-pod>
```

Primer: ako postoji novi `embedding-service` pod koji je `1/1 Running`, a stari `embedding-service` pod je ostao u `UnexpectedAdmissionError`, obrisati samo stari pod. Ne raditi novi `rollout restart` dok se ne proveri da li se aktivni podovi već sami oporavljaju.

Ako search/query ne radi zbog Ollama modela:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama list
kubectl -n repo-search exec deployment/ollama -- ollama pull gemma4:12b
```

Ako GPU nije vidljiv:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
kubectl describe node repo-search | Select-String nvidia.com/gpu
kubectl -n kube-system logs daemonset/nvidia-device-plugin-daemonset --tail=120
```

Ako je klaster obrisan komandom `minikube delete --profile repo-search`, baza se pravi iznova. Search tada radi, ali vraća prazne rezultate dok se ponovo ne napravi admin nalog i ne pokrene novi harvest.

## 11. Zaustavljanje

Zaustaviti Minikube klaster bez brisanja podataka:

```powershell
minikube stop -p repo-search
```

Ponovno pokretanje istog klastera:

```powershell
minikube start -p repo-search
kubectl config use-context repo-search
kubectl -n repo-search get pods
```

## 12. Brisanje

Obrisati aplikaciju i lokalne Kubernetes podatke:

```powershell
kubectl delete namespace repo-search
```

Obrisati ceo Minikube klaster:

```powershell
minikube delete --profile repo-search
```
