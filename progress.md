# What We Have Built So Far

---

## The Big Picture

We are building a Feature Flag Service. A feature flag is a switch that lets an engineering
team turn a feature ON or OFF for users without touching or redeploying the app code.
For example: "turn on dark mode for 50% of users" — no code deploy needed, just flip the flag.

The full system will have 3 services. Right now we have built 2 of them and set up the database.

---

## What Is Running Right Now

### PostgreSQL (the database) — running via Docker

We are using Docker to run a PostgreSQL database on our laptop. Docker means we did not
install Postgres directly on the machine — instead it runs inside a container, like a
lightweight isolated box. We can start it, stop it, or delete it cleanly without any mess.

The database has two tables:

- **flags table** — stores every feature flag. Each flag has a name, description,
  whether it is enabled (true/false), and a rollout percentage (0 to 100).
- **flag_audit table** — designed to store a history of every change made to a flag
  (who changed it and when). Not fully wired up yet, but the table exists.

---

## The Services We Built

### Service 1 — Flag Service (the main CRUD API)

This is the heart of the system. It lets you create, read, update, and delete feature flags.
It runs on port 8000. When you open `http://localhost:8000/docs` you get a Swagger UI
where you can test everything visually.

**Files inside `services/flag-service/`:**

- **schema.sql** — the SQL commands that create the two database tables. You run this
  once to set up the database before starting the service.

- **database.py** — handles the connection to PostgreSQL. Instead of opening a new
  connection for every single request (which is slow and wasteful), it maintains a
  pool of up to 10 connections that get reused. It is also thread-safe — meaning if
  multiple requests come in at the same time, they won't conflict.

- **models.py** — defines the shape of the data going in and out of the API using
  Pydantic. Three models:
  - FlagCreate — what you send when creating a flag (name, description, enabled, rollout_percentage)
  - FlagUpdate — what you send when updating a flag (all fields are optional — you only
    send what you want to change)
  - FlagResponse — what the API sends back to you (includes the id, timestamps, etc.)

- **main.py** — the actual FastAPI application. Contains all the API endpoints,
  the database logic, and the SNS publishing logic. This is the main file that runs
  when you start the server.

- **requirements.txt** — the list of all Python packages this service needs to be
  installed before it can run.

**What endpoints (API actions) the Flag Service has:**

| Endpoint | What it does |
|---|---|
| POST /flags | Create a new feature flag |
| GET /flags | Get a list of all flags |
| GET /flags/{id} | Get one specific flag by its ID |
| PATCH /flags/{id} | Update a flag (enable/disable it, change rollout %) |
| DELETE /flags/{id} | Delete a flag permanently |
| GET /health | Quick check — is the service alive? Returns "ok" |
| GET /ready | Deeper check — can the service reach the database? Returns "ready" or 503 |
| GET /metrics | Exposes live metrics (request count, latency) for Prometheus to scrape later |

**How rollout percentage works:**

When you set a flag to 50% rollout, not all users get it — only half. Which half? It is
deterministic — meaning the same user always gets the same result every time they ask.
It uses a hash of the flag name + user ID to decide. So user 123 always gets "enabled"
and user 456 always gets "disabled" — it never randomly flips for the same person.

**What happens when a flag changes:**

Every time a flag is created, updated, or deleted, the service tries to publish an event
to AWS SNS (a messaging service). This event eventually triggers a Slack notification.
If SNS is not configured (like on our local laptop), it silently skips it — the flag
still saves fine. This is by design so local development works without AWS.

---

### Service 2 — Evaluation Service (the fast read API)

This service answers one question: "Is flag X turned ON for user Y?"

It runs on port 8001. It is designed to respond in under 5 milliseconds — which is why
it uses Redis as a cache.

**Files inside `services/evaluation-service/`:**

- **main.py** — the FastAPI application with the evaluation logic.
- **requirements.txt** — the Python packages it needs.

**How the evaluation flow works:**

1. A request comes in: "Is dark_mode on for user 123?"
2. The service checks Redis (the cache) first — this is extremely fast.
3. If Redis has the answer (cache hit) → return it immediately.
4. If Redis does not have it (cache miss) → go to PostgreSQL, fetch the flag, store it
   in Redis for 60 seconds, then return the answer.
5. Apply the rollout percentage logic (same deterministic hash as above).

**What endpoints the Evaluation Service has:**

| Endpoint | What it does |
|---|---|
| GET /evaluate/{flag_name}?user_id=123 | Returns whether the flag is ON or OFF for that user |
| GET /health | Quick liveness check |

**What happens if Redis goes down:**

The service catches the error, logs a warning, and falls back to reading from PostgreSQL
directly. So it degrades gracefully — slower, but still works.

---

### Service 3 — Notification Lambda (the Slack notifier)

This is a small AWS Lambda function. It is not running locally — it only runs on AWS.
When a flag changes, SNS sends a message → SQS queues it → Lambda picks it up → sends
a Slack message to the team.

**Files inside `services/notification-lambda/`:**

- **handler.py** — the Lambda function code. Uses only Python's built-in libraries
  (no extra packages needed). If the Slack notification fails, it raises an error so
  SQS automatically retries the message — no silent failures.

---

## Tests Written

Tests live inside `services/flag-service/tests/`. They test every endpoint of the
Flag Service without needing a real database — the database is mocked (faked) so tests
run instantly and work on any machine.

**Test cases written:**

| Test | What it checks |
|---|---|
| test_health | /health returns 200 OK |
| test_create_flag_returns_201 | Creating a flag returns 201 and the correct data |
| test_create_flag_defaults | Creating a flag with just a name uses correct defaults (enabled=false, rollout=0) |
| test_list_flags_returns_all | GET /flags returns all flags in the database |
| test_list_flags_empty | GET /flags returns an empty list when there are no flags |
| test_get_flag | GET /flags/1 returns the correct flag |
| test_get_flag_not_found | GET /flags/999 returns 404 when the flag does not exist |
| test_update_flag | PATCH /flags/1 with {"enabled": false} correctly updates the flag |
| test_update_flag_not_found | PATCH /flags/999 returns 404 |
| test_update_flag_no_fields_returns_400 | PATCH with empty body returns 400 (nothing to update) |
| test_delete_flag | DELETE /flags/1 returns 204 (success, no body) |
| test_delete_flag_not_found | DELETE /flags/999 returns 404 |

All 12 tests pass. Run them with: `pytest tests/ -v`

---

## What We Have NOT Built Yet

These are planned for later phases of the project and will be done by hand as part of learning:

- Dockerfile and docker-compose (to containerise and run everything together with one command)
- Kubernetes manifests (to deploy to a cluster)
- Helm chart (to package the Kubernetes config)
- Terraform (to provision AWS infrastructure)
- GitHub Actions CI pipeline (to automate testing and deployment)
- ArgoCD (for GitOps — auto-deploy when code is pushed)
- Prometheus + Grafana dashboards (for monitoring and alerts)
- Istio service mesh (for mTLS and traffic splitting)
- OPA Gatekeeper + Falco + Vault (for security)
- Redis is not running yet — the evaluation service needs it to start properly

---

## Current Status (after Phase 1)

The backend code for all 3 services is written. The Flag Service is fully working and
tested. PostgreSQL is running in Docker. The next step is to get Redis running in Docker
and then start the Evaluation Service so we can test the full flag evaluation flow.

---

# Phase 2 — Kubernetes on minikube (completed 2026-06-10)

## What is Kubernetes and why we use it

Docker Compose runs containers on one machine. If that machine dies, everything dies.
Kubernetes (K8s) is a cluster manager — it runs your containers across many machines and
handles restarts, scaling, and networking automatically. You declare what you want ("I want
2 copies of flag-service running") and K8s makes it happen and keeps it that way forever.

minikube is a fake K8s cluster that runs on your laptop for free. It behaves exactly like
a real AWS EKS cluster, so everything you learn here transfers directly to production.

---

## Step 1 — Raw K8s manifests (the k8s/ folder)

We created these files one by one in this order. Each one is a YAML file that tells K8s
about one piece of your system.

### k8s/namespace.yaml
A namespace is just a logical folder inside K8s. Instead of everything landing in the
default namespace, all our resources live in `feature-flags`. Applied with:
`kubectl apply -f k8s/namespace.yaml`

### k8s/configmap.yaml
Stores non-secret environment variables as key-value pairs. Our flag-service reads
REDIS_HOST, AWS_REGION, and LOG_LEVEL from here. K8s injects them as env vars into
the pod automatically. Applied and verified with `kubectl describe configmap`.

### k8s/secret.yaml
Same as ConfigMap but for sensitive values (passwords). Values must be base64 encoded
before putting them in the YAML — use `echo -n "value" | base64` to encode.
K8s hides the values in `kubectl describe` output (shows byte count only, not the value).
Stores DATABASE_URL and DB_PASSWORD.

### k8s/deployment.yaml
The most important file. Tells K8s:
- Run 2 copies (replicas) of flag-service:latest
- Check /health every 10s — if it fails 3 times, restart the pod (liveness probe)
- Check /ready every 5s — if it fails, stop sending traffic to this pod (readiness probe)
- Limit each pod to 256Mi RAM and 250m CPU
- Use the flag-service-sa service account (not the default one)
- Inject all env vars from the ConfigMap and Secret

### k8s/postgres.yaml
Runs postgres:15 as a K8s Deployment AND creates a Service for it. The Service gives
postgres a stable hostname "postgres" inside the cluster — that is why DATABASE_URL has
@postgres:5432 in it. Without the Service, pods cannot find postgres by name.

### k8s/redis.yaml
Same pattern as postgres — runs redis:7-alpine and gives it the stable hostname "redis"
inside the cluster. The evaluation-service connects to redis:6379.

### k8s/evaluation-deployment.yaml
Runs the evaluation-service (port 8001) as a Deployment + Service. Gets REDIS_HOST and
DATABASE_URL injected as env vars. No readiness probe — only a liveness probe.

### k8s/service.yaml
ClusterIP service for flag-service on port 8000. ClusterIP means it is only reachable
inside the cluster. Other pods can call http://flag-service:8000. Not exposed to the internet.

### k8s/hpa.yaml
HorizontalPodAutoscaler — watches CPU across flag-service pods. If average CPU goes above
50%, K8s automatically adds more pods (up to max 5). If CPU drops, it scales back down to
min 2. Needs metrics-server addon: `minikube addons enable metrics-server`.
Check CPU with: `kubectl top pods -n feature-flags`

### k8s/rbac.yaml
Three resources in one file:
- ServiceAccount (flag-service-sa) — gives the pod an identity
- Role (flag-service-role) — defines what it can do: get/list configmaps and secrets
- RoleBinding — connects the identity to the permissions

Without RBAC, every pod uses the default service account with no defined permissions.
With RBAC, each service has its own identity and minimum required permissions.

---

## Step 2 — Helm chart (the helm/ folder)

After all raw YAML was working, we packaged everything into a Helm chart.

### What Helm solves
Without Helm: 8 separate `kubectl apply` commands to deploy. No easy way to have
different config for dev/staging/prod without duplicating files.

With Helm: one `helm install` command deploys everything. Values like replicas, image tag,
memory limits are variables — you change them per environment without touching templates.

### What we built
- `helm/feature-flag-chart/Chart.yaml` — chart metadata (name, version)
- `helm/feature-flag-chart/values.yaml` — all variables in one place
- `helm/feature-flag-chart/templates/deployment.yaml` — deployment with {{ .Values.xxx }}
- `helm/feature-flag-chart/templates/service.yaml`
- `helm/feature-flag-chart/templates/hpa.yaml`
- `helm/feature-flag-chart/templates/rbac.yaml`

### Helm commands used
```bash
helm lint helm/feature-flag-chart/           # validate the chart
helm template feature-flags helm/...         # preview rendered YAML without deploying
helm install feature-flags helm/... -n feature-flags    # deploy everything at once
helm upgrade feature-flags helm/... --set replicas=3    # change a value without editing files
helm history feature-flags -n feature-flags  # see all revisions
helm rollback feature-flags 1 -n feature-flags          # roll back to revision 1
```

---

## Important workflow notes

**Building images for minikube:**
minikube has its own Docker environment, separate from your Mac's Docker. K8s always
looks for images in minikube's Docker. Before running `docker build`, you must run:
`eval $(minikube docker-env)`
This points your terminal at minikube's Docker. Only affects the current terminal tab.

**After every minikube restart — reload the schema:**
Postgres has no PersistentVolume so data is lost on restart. After `minikube start`:
```
kubectl exec -n feature-flags deployment/postgres -- psql -U flaguser -d flags -c "
CREATE TABLE IF NOT EXISTS flags (...);
CREATE TABLE IF NOT EXISTS flag_audit (...);"
```

**Accessing services from your Mac:**
Pods are not reachable from your Mac directly. Use port-forward:
```
kubectl port-forward -n feature-flags svc/flag-service 8000:8000
kubectl port-forward -n feature-flags svc/evaluation-service 8001:8001
```

---

## Current Status (end of Phase 2)

All 6 pods running in minikube:
- flag-service x2
- evaluation-service x2
- postgres x1
- redis x1

Full end-to-end tested: POST a flag via flag-service → evaluate it via evaluation-service
→ Redis caches it → second evaluate is served from cache.

Helm chart installed and managing all resources: flag-service, evaluation-service, redis, HPA, RBAC.

Next: Phase 3 — Terraform to provision real AWS infrastructure (EKS, RDS, Redis, ECR).
