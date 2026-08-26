# Apple-silicon Mac development and demo

This profile is designed for a MacBook Air with Apple silicon, 16 GB unified
memory and 512 GB storage. It keeps the production-oriented NVIDIA Kubernetes
deployment unchanged.

## Architecture

- Docker Desktop runs the ARM64 application services, PostgreSQL and the
  embedding service.
- The embedding service uses CPU. Docker Desktop cannot expose the Apple GPU
  as CUDA or as PyTorch MPS to a Linux container.
- Ollama runs natively on macOS and uses Metal acceleration automatically.
- The database replica, background ingestion worker, containerized Ollama,
  Prometheus, Grafana and exporters are disabled in the default Mac profile to
  fit comfortably within 16 GB.

The Apple Neural Engine is not a transparent CUDA replacement. Using it would
require converting compatible models to Core ML/Core AI and maintaining a
separate inference implementation. That complexity is unnecessary for this
demo because native Ollama already accelerates LLM inference through Metal.

## One-time setup

Install current versions of:

1. Docker Desktop for Apple silicon.
2. Ollama for macOS.
3. Git and the Xcode command-line tools.

Give Docker Desktop 7-8 GB of memory. Keep at least 60 GB of SSD space free for
images, model caches and the database.

From the repository root:

```bash
cp .env.mac.example .env.mac
chmod +x scripts/mac-demo.sh
```

Replace `API_TOKEN` and `ADMIN_JWT_SECRET` in `.env.mac` before presenting the
application. Allow Docker containers to reach the native Ollama server:

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
```

Completely quit and reopen the Ollama app after setting the variable. Binding
to `0.0.0.0` can expose port 11434 to the local network, so use the macOS
firewall and do this only on a trusted network. To undo it later, run
`launchctl unsetenv OLLAMA_HOST` and restart Ollama.

Then start everything:

```bash
./scripts/mac-demo.sh up
```

The first start downloads the LLM, PyTorch wheels and the embedding model, so
perform it before demo day. The frontend is available at
`http://localhost:8091` and the gateway at `http://localhost:8090` by default.

Create the administrator once the services are ready:

```bash
docker compose --env-file .env.mac \
  -f docker-compose.microservices.yml \
  -f docker-compose.mac.yml \
  exec auth-service python -m microservices.auth_service.bootstrap_admin
```

## Daily use

```bash
./scripts/mac-demo.sh status
./scripts/mac-demo.sh logs gateway
./scripts/mac-demo.sh down
```

Do not use `down -v` unless the database and cached models should be deleted.

For harvesting new data, enable the worker explicitly:

```bash
docker compose --env-file .env.mac \
  -f docker-compose.microservices.yml \
  -f docker-compose.mac.yml \
  --profile ingestion up -d job-worker
```

Run ingestion before the presentation and stop the worker afterward. A demo
should use a pre-populated database so indexing does not compete with Ollama
for memory.

## Demo-day checklist

1. Start Docker Desktop and Ollama while connected to power.
2. Disable Low Power Mode.
3. Run `./scripts/mac-demo.sh up` before the audience arrives.
4. Confirm a known search returns results and an LLM-assisted query works.
5. Run `ollama ps` after an LLM query and confirm the processor is using the
   GPU rather than 100% CPU.
6. Keep the 12B model and observability profile stopped on a 16 GB machine.
7. Keep a database backup and a short screen recording as presentation
   fallbacks.
