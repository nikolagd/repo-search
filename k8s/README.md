# Kubernetes lokalno pokretanje

Ovo uputstvo je za pokretanje aplikacije na lokalnom Minikube Kubernetes klasteru.

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

## 2. Pokretanje Minikube klastera

CPU verzija:

```powershell
minikube start --driver=docker --profile repo-search
kubectl config use-context repo-search
```

GPU verzija:

U Docker Desktop-u proveriti da je NVIDIA runtime dostupan i postavljen kao default runtime. Otvoriti:

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

## 3. Build image-a unutar Minikube-a

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

## 4. Deploy

CPU deploy:

```powershell
kubectl apply -k k8s/
```

GPU deploy:

```powershell
kubectl apply -k k8s-gpu/
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

Prvo pokretanje može trajati duže jer Kubernetes pravi novi node, povlači bazne image-e,
pokreće storage, pravi Postgres volume-e i učitava modele. Backend image je velik zbog
Python/CUDA zavisnosti, a `ollama pull llama3.1:8b` dodatno preuzima nekoliko GB podataka.

## 5. Ollama model

Proveriti modele:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama list
```

Povući model ako nije već prisutan:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama pull llama3.1:8b
```

## 6. Otvaranje aplikacije

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

## 7. Korisne komande

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
```

Gateway health check:

```powershell
kubectl -n repo-search port-forward service/gateway 8090:8000
curl.exe -H "X-API-Key: replace_with_a_long_random_local_token" http://localhost:8090/api/health
```

Ako se health check pozove bez `X-API-Key` header-a, odgovor `401 Unauthorized` je očekivan.

Provera GPU-a u embedding podu:

```powershell
kubectl -n repo-search exec deployment/embedding-service -- python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

## 8. Observability i metrics-server

`metrics-server` je potreban za Kubernetes CPU/memory metrike, `kubectl top` i kasnije HPA pravila.
On ne skuplja aplikacione metrike kao sto su request latency, error rate, route throughput ili
service-to-service latency. Za to se koristi Prometheus scrape `/metrics` endpoint-a i Grafana.

U Minikube-u ukljuciti metrics-server:

```powershell
minikube addons enable metrics-server -p repo-search
kubectl top pods -n repo-search
kubectl top nodes
```

Ako `kubectl top` ne vraca podatke odmah, sacekati da se metrics-server rollout zavrsi:

```powershell
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
kubectl get apiservice v1beta1.metrics.k8s.io
```

Aplikacioni `/metrics` endpoint-i postoje na backend servisima:

```text
http://gateway:8000/metrics
http://auth-service:8000/metrics
http://catalog-service:8000/metrics
http://search-service:8000/metrics
http://query-service:8000/metrics
http://embedding-service:8000/metrics
http://job-service:8000/metrics
```

Prometheus, Grafana, Postgres exporter, kube-state-metrics i node-exporter su deo `k8s/05-observability.yaml` i primenjuju se kroz:

```powershell
kubectl apply -k k8s/
```

GPU overlay `k8s-gpu/` dodaje DCGM exporter za NVIDIA GPU metrike:

```powershell
kubectl apply -k k8s-gpu/
```

Otvaranje Prometheus-a:

```powershell
kubectl -n repo-search port-forward service/prometheus 9090:9090
```

Otvaranje Grafana-e:

```powershell
kubectl -n repo-search port-forward service/grafana 3000:3000
```

OpenTelemetry Collector i Jaeger su deo `k8s/06-tracing.yaml`. OpenTelemetry šalje trace podatke iz Python servisa ka collector-u, a collector ih prosleđuje u Jaeger.

Otvaranje Jaeger UI-a:

```powershell
kubectl -n repo-search port-forward service/jaeger 16686:16686
```

Zatim otvoriti:

```text
http://localhost:16686
```

U Jaeger-u se vidi jedan konkretan request ili job kroz servise, na primer `gateway -> search-service -> query-service -> embedding-service`.

## 9. Osnovni troubleshooting

Ako podovi nisu spremni:

```powershell
kubectl -n repo-search get pods
kubectl -n repo-search describe pod <ime-poda>
```

Ako je node `NotReady` odmah nakon pravljenja GPU klastera i Cilium pod stoji u init stanju,
proveriti Cilium i po potrebi restartovati njegov pod:

```powershell
kubectl get nodes
kubectl -n kube-system get pods
kubectl -n kube-system delete pod -l k8s-app=cilium
kubectl get nodes -w
```

Ako servis pada ili je u `CrashLoopBackOff` stanju:

```powershell
kubectl -n repo-search logs <ime-poda> --previous
kubectl -n repo-search logs <ime-poda>
```

Ako je klaster obrisan komandom `minikube delete --profile repo-search`, baza se pravi iznova.
Search tada radi, ali vraća prazne rezultate dok se ponovo ne napravi admin nalog i ne pokrene
novi harvest.

Ako frontend URL sa Minikube IP adresom ne radi na Windows-u:

```powershell
minikube service frontend -n repo-search -p repo-search
```

Koristiti `127.0.0.1` URL koji ta komanda prikaže.

Ako search/query ne radi zbog Ollama modela:

```powershell
kubectl -n repo-search exec deployment/ollama -- ollama list
kubectl -n repo-search exec deployment/ollama -- ollama pull llama3.1:8b
```

Ako GPU nije vidljiv:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Ako ova komanda ne radi, problem je u Docker Desktop/NVIDIA konfiguraciji, ne u Kubernetes manifestima.

```powershell
kubectl describe node repo-search | Select-String nvidia.com/gpu
kubectl -n kube-system logs daemonset/nvidia-device-plugin-daemonset --tail=120
```

Ako je rebuild urađen, ali aplikacija i dalje koristi stari kod:

```powershell
kubectl -n repo-search rollout restart deployment/search-service
kubectl -n repo-search rollout restart deployment/catalog-service
kubectl -n repo-search rollout restart deployment/job-worker
```

## 10. Zaustavljanje

Zaustaviti Minikube klaster bez brisanja podataka:

```powershell
minikube stop -p repo-search
```

Ovo zaustavlja Minikube container i sve Kubernetes podove/servise. Minikube profil i volume-i
ostaju na disku, tako da podaci iz baze ostaju sačuvani.

Ponovno pokretanje istog klastera:

```powershell
minikube start -p repo-search
kubectl config use-context repo-search
kubectl -n repo-search get pods
```

Ako koristiš Docker Compose umesto Kubernetes-a, arhivirano uputstvo je u `docs/docker-compose-microservices.md`.

## 11. Brisanje

Obrisati aplikaciju i lokalne Kubernetes podatke:

```powershell
kubectl delete namespace repo-search
```

Obrisati ceo Minikube klaster:

```powershell
minikube delete --profile repo-search
```
