# Feature Flag Service — DevOps Project

## What this project is
A production-grade Feature Flag Service that lets engineering teams toggle features 
ON/OFF for specific users without redeploying code. Built to learn every major DevOps 
tool hands-on.

## The 3 microservices
1. **Flag Service** (FastAPI) — create, update, delete, list flags. Stores in PostgreSQL RDS
2. **Evaluation Service** (FastAPI) — answers "is flag X ON for user Y?" Reads from Redis. Must respond under 5ms
3. **Notification Lambda** (Python) — triggered by SQS when a flag changes. Sends Slack message

## How the system flows
```
User creates/updates flag
    → Flag Service saves to RDS
    → Publishes event to SNS
    → SNS fans out to SQS
    → Lambda triggered by SQS
    → Slack message sent

User evaluates a flag
    → Evaluation Service checks Redis cache first
    → Cache miss → reads from RDS → caches result 60 seconds
    → Returns {flag: "dark_mode", enabled: true/false, user_id: "123"}
```

## Full tech stack and why each tool is used

| Tool | Why it is used |
|------|---------------|
| Docker | Containerise all 3 services |
| minikube | Run K8s locally for free during learning |
| Kubernetes (EKS) | Run flag service and evaluation service as Deployments |
| Helm | Package all K8s YAML into installable charts with dev/staging/prod values |
| Terraform | Provision all AWS infra as code — EKS, VPC, RDS, Redis, Lambda, SQS, SNS, ECR |
| AWS EKS | Managed Kubernetes — runs flag service and evaluation service |
| AWS RDS PostgreSQL | Stores all feature flags persistently |
| AWS ElastiCache Redis | Caches flag evaluations — must be under 5ms |
| AWS Lambda | Runs notification service serverlessly — fires on SQS events |
| AWS SQS | Queue between SNS and Lambda — decouples notification from flag service |
| AWS SNS | Publishes flag change events — fans out to SQS |
| AWS ECR | Stores Docker images for all services |
| AWS S3 | Stores Terraform remote state |
| GitHub Actions | CI pipeline — test, lint, Trivy scan, build, push to ECR, update image tag |
| ArgoCD | GitOps — watches git repo, auto-syncs changes to EKS |
| Prometheus | Scrapes metrics from both FastAPI services |
| Grafana | Dashboards — evaluation rate, latency p99, error rate, pod count |
| Alertmanager | Fires Slack alert if error rate > 5% or evaluation latency > 5ms |
| Istio | Service mesh — mTLS between services, traffic splitting for canary |
| OPA Gatekeeper | Blocks pods without resource limits or running as root |
| Falco | Runtime security — alerts when someone exec's into a pod |
| Vault | Secrets management — DB passwords and API keys, never in code |
| External Secrets Operator | Syncs Vault secrets into K8s at runtime |
| Trivy | Scans Docker images for CVEs in GitHub Actions pipeline |
| IRSA | IAM Roles for Service Accounts — pods get AWS permissions without static keys |
| Karpenter | Node autoscaler — provisions Spot nodes when pods are Pending |

## Project folder structure
```
feature-flag-service/
├── services/
│   ├── flag-service/
│   │   ├── main.py           ← FastAPI app
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── tests/
│   ├── evaluation-service/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── notification-lambda/
│       └── handler.py
├── helm/
│   └── feature-flag-chart/
│       ├── Chart.yaml
│       ├── values.yaml       ← defaults
│       ├── values-dev.yaml
│       ├── values-staging.yaml
│       ├── values-prod.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── deployment.yaml
│           ├── service.yaml
│           ├── configmap.yaml
│           ├── serviceaccount.yaml
│           ├── hpa.yaml
│           └── rbac.yaml
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── modules/
│       ├── vpc/
│       ├── eks/
│       ├── rds/
│       ├── redis/
│       ├── ecr/
│       └── lambda/
├── k8s/
│   ├── namespace.yaml
│   ├── alert-rules.yaml
│   └── pod-monitor.yaml
├── argocd/
│   ├── application.yaml
│   └── app-of-apps.yaml
├── istio/
│   ├── virtual-service.yaml
│   └── destination-rule.yaml
├── opa/
│   └── require-resource-limits.yaml
├── .github/
│   └── workflows/
│       ├── ci.yaml           ← test + scan + build + push
│       └── cd.yaml
├── docker-compose.yml        ← run locally for free
├── claude.md                 ← this file
└── README.md
```

## Database schema
```sql
CREATE TABLE flags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    enabled BOOLEAN DEFAULT FALSE,
    rollout_percentage INTEGER DEFAULT 0 CHECK (rollout_percentage BETWEEN 0 AND 100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE flag_audit (
    id SERIAL PRIMARY KEY,
    flag_name VARCHAR(100),
    action VARCHAR(50),
    changed_by VARCHAR(100),
    changed_at TIMESTAMP DEFAULT NOW()
);
```

## API endpoints

### Flag Service (port 8000)
```
POST   /flags              create a new flag
GET    /flags              list all flags
GET    /flags/{id}         get one flag
PATCH  /flags/{id}         update flag (enable/disable/rollout %)
DELETE /flags/{id}         delete flag
GET    /health             liveness probe
GET    /ready              readiness probe
GET    /metrics            Prometheus metrics
```

### Evaluation Service (port 8001)
```
GET    /evaluate/{flag_name}?user_id=123   is flag ON for this user?
GET    /health
GET    /metrics
```

## Key commands I use daily

```bash
# Local development
docker-compose up --build
docker-compose down

# Kubernetes
kubectl get pods -n feature-flags -w
kubectl describe pod <name> -n feature-flags
kubectl logs <pod> -n feature-flags -f
kubectl exec -it <pod> -n feature-flags -- sh

# Helm
helm lint helm/feature-flag-chart/
helm template feature-flags helm/feature-flag-chart/ -f values-dev.yaml
helm install feature-flags helm/feature-flag-chart/ -n feature-flags
helm upgrade feature-flags helm/feature-flag-chart/ -n feature-flags
helm rollback feature-flags 1
helm history feature-flags

# Terraform
terraform init
terraform plan
terraform apply
terraform destroy    # run every evening to save cost

# AWS
aws eks update-kubeconfig --name feature-flag-cluster --region ap-south-1
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-url>

# ArgoCD
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

## AWS region
ap-south-1 (Mumbai)

## Cost management
- Run terraform destroy every evening
- Run terraform apply every morning
- EKS costs ~₹200/day when running
- Lambda, SQS, SNS are free at this scale
- Set AWS billing alert at $20/month

## Environment variables needed

### Flag Service
```
DATABASE_URL=postgresql://flaguser:flagpass@host/flags
REDIS_HOST=redis-host
SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:account:feature-flag-events
AWS_REGION=ap-south-1
LOG_LEVEL=info
```

### Evaluation Service
```
DATABASE_URL=postgresql://flaguser:flagpass@host/flags
REDIS_HOST=redis-host
```

### Lambda
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
```

## Common errors and fixes

| Error | Cause | Fix |
|-------|-------|-----|
| OOMKilled / exit code 137 | Memory limit too low | Increase limits.memory in Helm values |
| ImagePullBackOff | Wrong image name or ECR auth expired | Check image tag, re-login to ECR |
| CrashLoopBackOff | App crashing on start | kubectl logs to see the actual error |
| Pending (no nodes) | Not enough cluster capacity | Check Karpenter logs, check Spot availability |
| 403 from K8s API | RBAC missing | Check ClusterRole and ClusterRoleBinding |
| Helm: resource already exists | Orphaned release from failed install | helm uninstall then reinstall |

## Build phases
1. Days 1–5: Docker + local app
2. Days 6–12: K8s on minikube + Helm
3. Days 13–22: AWS infra with Terraform
4. Days 23–30: GitHub Actions + ArgoCD
5. Days 31–37: Prometheus + Grafana + Istio
6. Days 38–45: OPA + Falco + Vault
7. Days 46–50: Demo + documentation + resume

## How to ask Claude for help
When you hit an error, paste:
1. The exact error message from kubectl describe or logs
2. Which phase/day you are on
3. Which file you were editing

Example: "Day 7, applying deployment.yaml, getting this error: [paste error]"

Claude will give you the exact fix with the reasoning.
