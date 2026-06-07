# Feature Flag Service — Full DevOps Project Plan

## What you are building
A production-grade Feature Flag Service — a system that lets engineering teams 
turn features ON/OFF for specific users without redeploying code.

## The 3 microservices
- **Flag Service** — create, update, delete flags. Stores in PostgreSQL (RDS)
- **Evaluation Service** — answers "is this flag ON for user X?" Reads from Redis. Must be under 5ms
- **Notification Service** — Lambda function. When a flag changes, sends Slack message to subscribed teams

## Tools you will use
Terraform, AWS EKS, Helm, GitHub Actions, ArgoCD, Lambda, SQS, SNS, 
Redis (ElastiCache), RDS PostgreSQL, Vault, Prometheus, Grafana, 
Istio, Trivy, OPA Gatekeeper, Falco, Docker, minikube, kubectl

---

# PHASE 1 — Local Setup & Docker (Days 1–5)
**Cost: ₹0 — everything runs on your laptop**

## Day 1 — Install all tools on your laptop

### Install these one by one
```bash
# 1. Docker Desktop
# Go to https://docker.com/products/docker-desktop → download for your OS
# Verify:
docker --version

# 2. minikube (local Kubernetes)
# Mac:
brew install minikube
# Windows: download from https://minikube.sigs.k8s.io/docs/start/
minikube version

# 3. kubectl (talk to Kubernetes)
# Mac:
brew install kubectl
# Windows: https://kubernetes.io/docs/tasks/tools/install-kubectl-windows/
kubectl version --client

# 4. Helm (package manager for K8s)
# Mac:
brew install helm
# Windows:
choco install kubernetes-helm
helm version

# 5. Terraform
# Mac:
brew install terraform
# Windows:
choco install terraform
terraform --version

# 6. AWS CLI
# Mac:
brew install awscli
# Windows: https://aws.amazon.com/cli/
aws --version
```

### Milestone Day 1
All 6 tools installed. Run these — all should return version numbers, no errors.
```bash
docker --version
minikube version
kubectl version --client
helm version
terraform --version
aws --version
```

---

## Day 2 — Write the Flag Service (the actual app)

### Project folder structure
```
feature-flag-service/
├── services/
│   ├── flag-service/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── evaluation-service/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── notification-lambda/
│       ├── handler.py
│       └── requirements.txt
├── helm/
├── terraform/
├── .github/
└── README.md
```

### Flag Service — main.py
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import psycopg2, os, json, boto3

app = FastAPI(title="Feature Flag Service")

def get_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

class Flag(BaseModel):
    name: str
    description: str
    enabled: bool = False
    rollout_percentage: int = 0  # 0-100

@app.get("/health")
def health():
    return {"status": "ok", "service": "flag-service"}

@app.post("/flags")
def create_flag(flag: Flag):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO flags (name, description, enabled, rollout_percentage, created_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (flag.name, flag.description, flag.enabled, flag.rollout_percentage, datetime.now()))
    flag_id = cur.fetchone()[0]
    conn.commit()
    
    # Publish event to SNS so Lambda notifies teams
    publish_flag_event("FLAG_CREATED", flag.name, flag.enabled)
    
    return {"id": flag_id, "name": flag.name, "enabled": flag.enabled}

@app.get("/flags")
def list_flags():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, enabled, rollout_percentage FROM flags")
    flags = cur.fetchall()
    return [{"id": f[0], "name": f[1], "description": f[2], 
             "enabled": f[3], "rollout_percentage": f[4]} for f in flags]

@app.patch("/flags/{flag_id}")
def update_flag(flag_id: int, flag: Flag):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE flags SET enabled=%s, rollout_percentage=%s WHERE id=%s
    """, (flag.enabled, flag.rollout_percentage, flag_id))
    conn.commit()
    publish_flag_event("FLAG_UPDATED", flag.name, flag.enabled)
    return {"id": flag_id, "updated": True}

@app.delete("/flags/{flag_id}")
def delete_flag(flag_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT name FROM flags WHERE id=%s", (flag_id,))
    flag = cur.fetchone()
    cur.execute("DELETE FROM flags WHERE id=%s", (flag_id,))
    conn.commit()
    publish_flag_event("FLAG_DELETED", flag[0], False)
    return {"deleted": True}

def publish_flag_event(event_type: str, flag_name: str, enabled: bool):
    try:
        sns = boto3.client('sns', region_name=os.getenv("AWS_REGION", "ap-south-1"))
        sns.publish(
            TopicArn=os.getenv("SNS_TOPIC_ARN"),
            Message=json.dumps({"event": event_type, "flag": flag_name, "enabled": enabled}),
            Subject=f"Feature Flag {event_type}"
        )
    except Exception as e:
        print(f"SNS publish failed (ok locally): {e}")
```

### Flag Service — requirements.txt
```
fastapi==0.104.1
uvicorn==0.24.0
psycopg2-binary==2.9.9
pydantic==2.5.0
boto3==1.34.0
```

### Milestone Day 2
Flag service code written. Run locally:
```bash
cd services/flag-service
pip install -r requirements.txt
# Set a fake DB url for now
DATABASE_URL=postgresql://user:pass@localhost/flags uvicorn main:app --reload
# Visit http://localhost:8000/docs — Swagger UI should open
```

---

## Day 3 — Write the Evaluation Service

### Evaluation Service — main.py
```python
from fastapi import FastAPI
import redis, psycopg2, os, hashlib

app = FastAPI(title="Evaluation Service")

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379, decode_responses=True
)

@app.get("/health")
def health():
    return {"status": "ok", "service": "evaluation-service"}

@app.get("/evaluate/{flag_name}")
def evaluate_flag(flag_name: str, user_id: str):
    """
    Is this flag ON for this specific user?
    Returns: {"flag": "dark_mode", "enabled": true/false, "user_id": "123"}
    """
    # Check Redis cache first — must be fast (under 5ms)
    cached = r.get(f"flag:{flag_name}")
    
    if cached:
        flag_data = eval(cached)  # in prod use json.loads
    else:
        # Cache miss — read from DB and cache it
        flag_data = get_flag_from_db(flag_name)
        if flag_data:
            r.setex(f"flag:{flag_name}", 60, str(flag_data))  # cache 60 seconds
    
    if not flag_data:
        return {"flag": flag_name, "enabled": False, "reason": "flag not found"}
    
    if not flag_data["enabled"]:
        return {"flag": flag_name, "enabled": False, "user_id": user_id}
    
    # Rollout percentage — deterministic per user (same user always gets same result)
    if flag_data["rollout_percentage"] < 100:
        user_hash = int(hashlib.md5(f"{flag_name}{user_id}".encode()).hexdigest(), 16)
        user_bucket = user_hash % 100
        if user_bucket >= flag_data["rollout_percentage"]:
            return {"flag": flag_name, "enabled": False, 
                    "reason": "not in rollout", "user_id": user_id}
    
    return {"flag": flag_name, "enabled": True, "user_id": user_id}

def get_flag_from_db(flag_name: str):
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name, enabled, rollout_percentage FROM flags WHERE name=%s", 
            (flag_name,)
        )
        row = cur.fetchone()
        if row:
            return {"name": row[0], "enabled": row[1], "rollout_percentage": row[2]}
    except Exception as e:
        print(f"DB error: {e}")
    return None
```

### Notification Lambda — handler.py
```python
import json, os
import urllib.request

def handler(event, context):
    """
    Triggered by SQS when a flag changes.
    Sends a Slack message to notify the team.
    """
    for record in event['Records']:
        body = json.loads(record['body'])
        message = json.loads(body['Message'])
        
        flag_name = message['flag']
        event_type = message['event']
        enabled = message['enabled']
        
        status = "✅ ENABLED" if enabled else "❌ DISABLED"
        slack_message = {
            "text": f"🚩 Feature Flag Update\n"
                   f"*Flag:* `{flag_name}`\n"
                   f"*Event:* {event_type}\n"
                   f"*Status:* {status}"
        }
        
        webhook_url = os.environ['SLACK_WEBHOOK_URL']
        data = json.dumps(slack_message).encode('utf-8')
        req = urllib.request.Request(webhook_url, data=data,
                                    headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req)
    
    return {"statusCode": 200}
```

### Milestone Day 3
All 3 services written. Code exists in the folder structure.

---

## Day 4 — Dockerise everything

### Flag Service Dockerfile
```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY . .
USER nobody
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml (run everything locally)
```yaml
version: '3.8'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: flags
      POSTGRES_USER: flaguser
      POSTGRES_PASSWORD: flagpass
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  flag-service:
    build: ./services/flag-service
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://flaguser:flagpass@postgres/flags
      REDIS_HOST: redis
      SNS_TOPIC_ARN: "arn:aws:sns:fake"
    depends_on:
      - postgres
      - redis

  evaluation-service:
    build: ./services/evaluation-service
    ports:
      - "8001:8000"
    environment:
      DATABASE_URL: postgresql://flaguser:flagpass@postgres/flags
      REDIS_HOST: redis
    depends_on:
      - postgres
      - redis
```

### Build and run
```bash
docker-compose up --build

# Test it
curl http://localhost:8000/health
curl http://localhost:8001/health
curl -X POST http://localhost:8000/flags \
  -H "Content-Type: application/json" \
  -d '{"name":"dark_mode","description":"Dark mode UI","enabled":true,"rollout_percentage":50}'

curl "http://localhost:8001/evaluate/dark_mode?user_id=12345"
```

### Milestone Day 4
docker-compose up starts everything. You can create a flag and evaluate it. Both services healthy.

---

## Day 5 — Push to GitHub

```bash
# Create repo on github.com first, then:
git init
git add .
git commit -m "initial: feature flag service with docker"
git remote add origin https://github.com/YOUR_USERNAME/feature-flag-service.git
git push -u origin main
```

Create a `.gitignore`:
```
__pycache__/
*.pyc
.env
.terraform/
*.tfstate
*.tfstate.backup
node_modules/
.DS_Store
```

### Milestone Day 5
Code is on GitHub. Repo is public.

---

# PHASE 2 — Kubernetes on minikube (Days 6–12)
**Cost: ₹0 — still on your laptop**

## Day 6 — Start minikube, understand K8s objects

```bash
minikube start --memory=4096 --cpus=2
kubectl get nodes   # should show 1 node, Ready

# Enable ingress addon
minikube addons enable ingress
```

### Create the database schema
```sql
-- run this in your postgres container
CREATE TABLE flags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    enabled BOOLEAN DEFAULT FALSE,
    rollout_percentage INTEGER DEFAULT 0,
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

### Milestone Day 6
minikube running. kubectl get nodes shows Ready.

---

## Day 7 — Write K8s manifests

### Namespace
```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: feature-flags
```

### ConfigMap
```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: flag-service-config
  namespace: feature-flags
data:
  REDIS_HOST: "redis-service"
  AWS_REGION: "ap-south-1"
  LOG_LEVEL: "info"
```

### Secret
```yaml
# k8s/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: flag-service-secrets
  namespace: feature-flags
type: Opaque
stringData:
  DATABASE_URL: "postgresql://flaguser:flagpass@postgres-service/flags"
  SLACK_WEBHOOK_URL: "https://hooks.slack.com/your-webhook"
```

### Deployment
```yaml
# k8s/deployment-flag-service.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flag-service
  namespace: feature-flags
spec:
  replicas: 2
  selector:
    matchLabels:
      app: flag-service
  template:
    metadata:
      labels:
        app: flag-service
    spec:
      containers:
      - name: flag-service
        image: flag-service:latest
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef:
            name: flag-service-config
        - secretRef:
            name: flag-service-secrets
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 15
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
```

### Service
```yaml
# k8s/service-flag-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: flag-service
  namespace: feature-flags
spec:
  selector:
    app: flag-service
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

### Apply everything
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment-flag-service.yaml
kubectl apply -f k8s/service-flag-service.yaml

kubectl get pods -n feature-flags -w
```

### Milestone Day 7
Pods running. kubectl get pods shows 2/2 Running.

---

## Day 8 — Break things intentionally (most important day)

```bash
# Break 1: OOMKilled — set memory limit too low
# Edit deployment: limits.memory: "10Mi"
kubectl apply -f k8s/deployment-flag-service.yaml
kubectl get pods -n feature-flags -w
# Watch it OOMKilled. Read:
kubectl describe pod <pod-name> -n feature-flags
# Fix: set back to 256Mi

# Break 2: Wrong image name
# Edit deployment: image: flag-service:doesnotexist
kubectl apply -f k8s/deployment-flag-service.yaml
# Watch ImagePullBackOff error
kubectl describe pod <pod-name> -n feature-flags
# Fix: correct the image name

# Break 3: Kill a pod manually
kubectl delete pod <pod-name> -n feature-flags
# Watch it come back automatically — reconciliation loop
kubectl get pods -n feature-flags -w

# Break 4: Remove the secret, watch crash
kubectl delete secret flag-service-secrets -n feature-flags
# Pod crashes — missing env vars
# Add secret back
kubectl apply -f k8s/secret.yaml
```

### Milestone Day 8
You have seen OOMKilled, ImagePullBackOff, CrashLoopBackOff with your own eyes. You know how to read kubectl describe. These errors will never surprise you again.

---

## Day 9 — HPA + Resource management

```yaml
# k8s/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: flag-service-hpa
  namespace: feature-flags
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: flag-service
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

```bash
kubectl apply -f k8s/hpa.yaml

# Enable metrics server
minikube addons enable metrics-server

# Watch HPA
kubectl get hpa -n feature-flags -w
```

### Milestone Day 9
HPA created. kubectl get hpa shows current replicas.

---

## Day 10–11 — RBAC + ServiceAccount

```yaml
# k8s/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: flag-service-sa
  namespace: feature-flags
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: flag-service-role
  namespace: feature-flags
rules:
- apiGroups: [""]
  resources: ["configmaps", "secrets"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: flag-service-rolebinding
  namespace: feature-flags
subjects:
- kind: ServiceAccount
  name: flag-service-sa
roleRef:
  kind: Role
  name: flag-service-role
  apiGroup: rbac.authorization.k8s.io
```

### Milestone Day 10–11
ServiceAccount created. Pod uses it. kubectl describe pod shows ServiceAccount: flag-service-sa.

---

## Day 12 — Helm chart

```bash
helm create feature-flag-chart
```

Convert all your K8s YAML files into Helm templates. Add values.yaml:

```yaml
# helm/feature-flag-chart/values.yaml
replicaCount: 2
image:
  repository: flag-service
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80

resources:
  limits:
    cpu: 200m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

config:
  redisHost: "redis-service"
  awsRegion: "ap-south-1"
  logLevel: "info"
```

```bash
# Lint first — always
helm lint helm/feature-flag-chart/

# Preview rendered YAML before applying
helm template feature-flags helm/feature-flag-chart/ -f helm/feature-flag-chart/values.yaml

# Install
helm install feature-flags helm/feature-flag-chart/ -n feature-flags

# Upgrade
helm upgrade feature-flags helm/feature-flag-chart/ -n feature-flags

# Rollback
helm rollback feature-flags 1

# History
helm history feature-flags
```

### Milestone Day 12
helm install works. helm rollback works. helm lint passes zero warnings.

---

# PHASE 3 — AWS Infrastructure with Terraform (Days 13–22)
**Cost: starts here — destroy every evening**

## Day 13 — AWS account setup

```bash
# 1. Create AWS account at aws.amazon.com
#    Use PAN card for identity
#    Add debit/credit card (₹83 charge, immediately refunded)

# 2. Set billing alert FIRST — before anything else
# AWS Console → Billing → Budgets → Create Budget
# Set $20/month alert — you get email before overspending

# 3. Create IAM user (never use root account)
# AWS Console → IAM → Users → Create User
# Attach policy: AdministratorAccess (for learning only)
# Create access key → download CSV

# 4. Configure AWS CLI
aws configure
# Enter: Access Key ID, Secret Access Key, Region: ap-south-1, Format: json

# Test
aws sts get-caller-identity
```

### Milestone Day 13
aws sts get-caller-identity returns your account ID. Billing alert set.

---

## Day 14–15 — Terraform VPC + ECR

```hcl
# terraform/main.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket = "your-tfstate-bucket"
    key    = "feature-flags/terraform.tfstate"
    region = "ap-south-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# terraform/variables.tf
variable "aws_region" {
  default = "ap-south-1"
}
variable "cluster_name" {
  default = "feature-flag-cluster"
}
variable "environment" {
  default = "dev"
}
```

```hcl
# terraform/modules/vpc/main.tf
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["ap-south-1a", "ap-south-1b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true   # save cost — one NAT not two
  enable_dns_hostnames = true

  tags = {
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    Environment = var.environment
  }
}
```

```hcl
# terraform/modules/ecr/main.tf
resource "aws_ecr_repository" "flag_service" {
  name                 = "flag-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true   # auto scan for CVEs
  }
}

resource "aws_ecr_repository" "evaluation_service" {
  name = "evaluation-service"
  image_scanning_configuration {
    scan_on_push = true
  }
}
```

```bash
# Create S3 bucket for tfstate first (one time)
aws s3 mb s3://your-tfstate-bucket-unique-name --region ap-south-1

terraform init
terraform plan    # see what will be created — always check before apply
terraform apply   # type 'yes'
```

### Milestone Day 14–15
VPC created. ECR repos created. terraform state in S3. terraform plan shows no changes after apply.

---

## Day 16–17 — EKS Cluster

```hcl
# terraform/modules/eks/main.tf
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "20.0.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.29"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    general = {
      min_size       = 1
      max_size       = 3
      desired_size   = 2
      instance_types = ["t3.medium"]
      capacity_type  = "SPOT"   # save 70% cost
    }
  }

  tags = {
    Environment = var.environment
  }
}
```

```bash
terraform apply

# Connect kubectl to EKS
aws eks update-kubeconfig \
  --name feature-flag-cluster \
  --region ap-south-1

kubectl get nodes   # should show 2 nodes, Ready
```

### Milestone Day 16–17
kubectl get nodes shows real AWS nodes. Not minikube — real EKS.

---

## Day 18 — RDS + ElastiCache

```hcl
# terraform/modules/rds/main.tf
resource "aws_db_instance" "flags_db" {
  identifier        = "feature-flags-db"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.micro"   # cheapest option
  allocated_storage = 20
  
  db_name  = "flags"
  username = "flaguser"
  password = var.db_password   # from variables, never hardcode

  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.flags.name

  skip_final_snapshot = true   # for dev — lets us destroy cleanly
  
  tags = { Environment = var.environment }
}

# terraform/modules/redis/main.tf
resource "aws_elasticache_cluster" "flags_redis" {
  cluster_id           = "feature-flags-redis"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
}
```

### Milestone Day 18
RDS and Redis running on AWS. Can connect from your machine.

---

## Day 19–20 — Lambda + SQS + SNS

```hcl
# terraform/modules/lambda/main.tf
resource "aws_sns_topic" "flag_events" {
  name = "feature-flag-events"
}

resource "aws_sqs_queue" "flag_notifications" {
  name                      = "flag-notifications"
  message_retention_seconds = 86400
}

resource "aws_sns_topic_subscription" "sqs_subscription" {
  topic_arn = aws_sns_topic.flag_events.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.flag_notifications.arn
}

resource "aws_lambda_function" "notification" {
  filename         = "notification-lambda.zip"
  function_name    = "flag-notification"
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.handler"
  runtime          = "python3.11"

  environment {
    variables = {
      SLACK_WEBHOOK_URL = var.slack_webhook_url
    }
  }
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.flag_notifications.arn
  function_name    = aws_lambda_function.notification.arn
  batch_size       = 10
}
```

```bash
# Package Lambda
cd services/notification-lambda
zip -r notification-lambda.zip handler.py
mv notification-lambda.zip ../../terraform/

terraform apply
```

### Milestone Day 19–20
Create a flag → SNS fires → SQS receives → Lambda runs → Slack message appears. Full event chain working.

---

## Day 21–22 — Deploy Helm chart to EKS

```bash
# Build and push images to ECR
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$AWS_ACCOUNT.dkr.ecr.ap-south-1.amazonaws.com"

# Login to ECR
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin $ECR_URI

# Build and push flag-service
docker build -t flag-service:v1.0.0 services/flag-service/
docker tag flag-service:v1.0.0 $ECR_URI/flag-service:v1.0.0
docker push $ECR_URI/flag-service:v1.0.0

# Deploy via Helm
helm install feature-flags helm/feature-flag-chart/ \
  --namespace feature-flags \
  --create-namespace \
  -f helm/feature-flag-chart/values-prod.yaml \
  --set image.repository=$ECR_URI/flag-service \
  --set image.tag=v1.0.0

kubectl get pods -n feature-flags -w
```

### Milestone Day 21–22
All pods running on real EKS. Feature flag service accessible via AWS Load Balancer URL.

---

# PHASE 4 — CI/CD with GitHub Actions + ArgoCD (Days 23–30)

## Day 23–24 — GitHub Actions CI pipeline

```yaml
# .github/workflows/ci.yaml
name: CI Pipeline

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r services/flag-service/requirements.txt
          pip install pytest httpx
      
      - name: Run tests
        run: pytest tests/ -v

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install ruff
      - run: ruff check services/

  security-scan:
    needs: [test, lint]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build image
        run: docker build -t flag-service:${{ github.sha }} services/flag-service/
      
      - name: Trivy scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: flag-service:${{ github.sha }}
          exit-code: '1'
          severity: 'HIGH,CRITICAL'

  build-push:
    needs: security-scan
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ap-south-1
      
      - name: Login to ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2
      
      - name: Build and push
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
        run: |
          docker build -t $ECR_REGISTRY/flag-service:${{ github.sha }} services/flag-service/
          docker push $ECR_REGISTRY/flag-service:${{ github.sha }}
      
      - name: Update image tag in Helm values
        run: |
          sed -i "s/tag:.*/tag: ${{ github.sha }}/" helm/feature-flag-chart/values-prod.yaml
          git config --global user.email "github-actions@github.com"
          git config --global user.name "GitHub Actions"
          git add helm/feature-flag-chart/values-prod.yaml
          git commit -m "deploy: update image to ${{ github.sha }}"
          git push
```

### Add GitHub secrets
```
GitHub repo → Settings → Secrets → Actions:
AWS_ACCESS_KEY_ID     → your IAM user access key
AWS_SECRET_ACCESS_KEY → your IAM user secret key
SLACK_WEBHOOK_URL     → your Slack webhook
```

### Milestone Day 23–24
Push to main → pipeline runs → all 4 jobs pass → image in ECR → values-prod.yaml updated automatically.

---

## Day 25–27 — ArgoCD

```bash
# Install ArgoCD on EKS
kubectl create namespace argocd
helm repo add argo https://argoproj.github.io/argo-helm
helm install argocd argo/argo-cd \
  --namespace argocd \
  --set server.service.type=LoadBalancer

# Get ArgoCD URL
kubectl get svc argocd-server -n argocd

# Get initial password
kubectl get secret argocd-initial-admin-secret \
  -n argocd \
  -o jsonpath="{.data.password}" | base64 -d
```

```yaml
# argocd/application.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: feature-flag-service
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/YOUR_USERNAME/feature-flag-service
    targetRevision: main
    path: helm/feature-flag-chart
    helm:
      valueFiles:
        - values-prod.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: feature-flags
  syncPolicy:
    automated:
      prune: true
      selfHeal: true    # if someone manually changes cluster, ArgoCD reverts it
    syncOptions:
      - CreateNamespace=true
```

```bash
kubectl apply -f argocd/application.yaml
```

### Milestone Day 25–27
Push one line of code change. Watch: GitHub Actions runs → image built → tag updated in git → ArgoCD detects change → deploys to EKS automatically. Zero manual steps.

---

## Day 28–30 — Multi-environment (dev + staging + prod)

```yaml
# argocd/app-of-apps.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: feature-flag-environments
  namespace: argocd
spec:
  generators:
  - list:
      elements:
      - env: dev
        namespace: feature-flags-dev
        valuesFile: values-dev.yaml
      - env: staging
        namespace: feature-flags-staging
        valuesFile: values-staging.yaml
      - env: prod
        namespace: feature-flags-prod
        valuesFile: values-prod.yaml
  template:
    metadata:
      name: 'feature-flags-{{env}}'
    spec:
      source:
        repoURL: https://github.com/YOUR_USERNAME/feature-flag-service
        path: helm/feature-flag-chart
        helm:
          valueFiles:
            - '{{valuesFile}}'
      destination:
        server: https://kubernetes.default.svc
        namespace: '{{namespace}}'
      syncPolicy:
        automated:
          selfHeal: true
```

### Milestone Day 28–30
Three environments in ArgoCD — dev, staging, prod. Each deploys independently. ArgoCD UI shows all three.

---

# PHASE 5 — Observability (Days 31–37)

## Day 31–33 — Prometheus + Grafana

```bash
# Install kube-prometheus-stack (Prometheus + Grafana + Alertmanager in one)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.service.type=LoadBalancer
```

### Add metrics to Flag Service
```python
# Add to flag-service/main.py
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Histogram

flag_evaluations = Counter(
    'flag_evaluations_total',
    'Total flag evaluations',
    ['flag_name', 'result']
)

evaluation_duration = Histogram(
    'flag_evaluation_duration_seconds',
    'Time to evaluate a flag'
)

Instrumentator().instrument(app).expose(app)
```

```yaml
# Alertmanager rule
# k8s/alert-rules.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: flag-service-alerts
  namespace: feature-flags
spec:
  groups:
  - name: flag-service
    rules:
    - alert: HighErrorRate
      expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Flag service error rate > 5%"
    
    - alert: SlowEvaluation
      expr: histogram_quantile(0.99, flag_evaluation_duration_seconds_bucket) > 0.005
      for: 2m
      annotations:
        summary: "Flag evaluation p99 > 5ms — Redis might be down"
```

### Grafana dashboard panels to build
```
Panel 1: Flag evaluations per second (by flag name)
Panel 2: Evaluation latency p99 (must be under 5ms)
Panel 3: API error rate (%)
Panel 4: Pod count over time
Panel 5: Node CPU usage
Panel 6: Most evaluated flags (top 10)
```

### Milestone Day 31–33
Grafana shows live metrics. Alert fires to Slack when you intentionally crash the service.

---

## Day 34–37 — Istio service mesh

```bash
# Install Istio
curl -L https://istio.io/downloadIstio | sh -
istioctl install --set profile=demo -y

# Enable Istio for your namespace (auto-injects sidecar)
kubectl label namespace feature-flags istio-injection=enabled

# Restart pods to get sidecar injected
kubectl rollout restart deployment -n feature-flags
```

```yaml
# istio/virtual-service.yaml — canary: 10% to new version
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: flag-service
  namespace: feature-flags
spec:
  hosts:
  - flag-service
  http:
  - route:
    - destination:
        host: flag-service
        subset: stable
      weight: 90
    - destination:
        host: flag-service
        subset: canary
      weight: 10   # 10% goes to new version
```

### Milestone Day 34–37
kubectl get pods -n feature-flags shows 2 containers per pod (app + Istio sidecar). Traffic splitting working — 10% hits new version.

---

# PHASE 6 — Security (Days 38–45)

## Day 38–40 — OPA Gatekeeper

```bash
helm repo add gatekeeper https://open-policy-agent.github.io/gatekeeper/charts
helm install gatekeeper gatekeeper/gatekeeper --namespace gatekeeper-system --create-namespace
```

```yaml
# opa/require-resource-limits.yaml
# Blocks any pod without resource limits
apiVersion: constraints.gatekeeper.sh/v1beta1
kind: K8sRequiredResources
metadata:
  name: require-resource-limits
spec:
  match:
    kinds:
    - apiGroups: ["apps"]
      kinds: ["Deployment"]
  parameters:
    limits: ["cpu", "memory"]
    requests: ["cpu", "memory"]
```

```bash
# Test it — try to deploy without limits
# It should be BLOCKED with a clear error message
```

## Day 41–43 — Falco (runtime security)

```bash
helm repo add falcosecurity https://falcosecurity.github.io/charts
helm install falco falcosecurity/falco \
  --namespace falco \
  --create-namespace \
  --set falcosidekick.enabled=true \
  --set falcosidekick.config.slack.webhookurl=$SLACK_WEBHOOK
```

```yaml
# Custom Falco rule — alert if someone runs shell in a pod
- rule: Shell in Container
  desc: Someone opened a shell in a running container
  condition: spawned_process and container and shell_procs
  output: "Shell opened in container (user=%user.name container=%container.name)"
  priority: WARNING
```

```bash
# Test it — exec into a pod and run a command
kubectl exec -it <pod-name> -n feature-flags -- sh
# Slack should get an alert within seconds
```

## Day 44–45 — Vault

```bash
# Install Vault
helm repo add hashicorp https://helm.releases.hashicorp.com
helm install vault hashicorp/vault \
  --namespace vault \
  --create-namespace \
  --set server.dev.enabled=true   # dev mode for learning
```

```bash
# Store DB password in Vault
vault kv put secret/feature-flags \
  db_password="your-real-password" \
  slack_webhook="https://hooks.slack.com/..."

# Install External Secrets Operator
helm install external-secrets \
  external-secrets/external-secrets \
  --namespace external-secrets \
  --create-namespace
```

### Milestone Day 44–45
Zero secrets in K8s Secrets or ConfigMaps. All secrets come from Vault at runtime. OPA blocks pods without resource limits. Falco sends Slack alert when you exec into a pod.

---

# PHASE 7 — Polish + Demo (Days 46–50)

## Day 46–47 — Clean up and document

```
README.md must have:
- What the project does (2 sentences)
- Architecture diagram (draw on excalidraw.com — free)
- List of all tools used and WHY each one is used
- How to run locally (docker-compose up)
- How to deploy to AWS (terraform apply + helm install)
- Demo video link (record with Loom — free)
```

## Day 48 — Record your demo

5 minute demo script:
```
1. Show architecture diagram — explain what each service does
2. Create a feature flag via API — "dark_mode, 50% rollout"
3. Evaluate it for 5 different user IDs — show some get true, some false
4. Show Slack message that fired when flag was created (Lambda)
5. Show Grafana dashboard — evaluation rate, latency under 5ms
6. Make a code change — push to GitHub
7. Show GitHub Actions pipeline running (test → scan → build → push)
8. Show ArgoCD syncing automatically
9. Show pod updated in EKS — zero downtime
10. Run terraform destroy — everything gone in 8 minutes
```

## Day 49–50 — Write resume bullets

```
• Built a production-grade Feature Flag Service on AWS EKS serving 
  flag evaluations under 5ms using Redis caching, deployed across 
  dev/staging/prod environments via GitOps

• Provisioned complete AWS infrastructure (EKS, RDS, ElastiCache, 
  Lambda, SQS, SNS) using Terraform — fully reproducible from 
  terraform apply in under 20 minutes

• Built GitOps CI/CD pipeline with GitHub Actions (test → Trivy 
  security scan → ECR push) and ArgoCD (auto-sync, self-heal) — 
  zero manual deployments

• Implemented event-driven notification system: flag changes publish 
  to SNS → SQS → Lambda → Slack, fully decoupled from K8s workloads

• Deployed Istio service mesh with mTLS between all services and 
  traffic splitting for canary releases (90/10 traffic split)

• Enforced security with OPA Gatekeeper (blocks pods without resource 
  limits), Falco (runtime threat detection), and Vault (zero static 
  credentials — all secrets injected at runtime)
```

---

## terraform destroy — run every evening

```bash
# Every evening before you stop working
terraform destroy

# Every morning to start again
terraform apply
```

This keeps your AWS bill under ₹150/day during active development.

---

# Total timeline

| Phase | Days | What you learn | Cost |
|-------|------|----------------|------|
| 1 — Docker | 1–5 | Docker, containers, docker-compose | ₹0 |
| 2 — K8s minikube | 6–12 | K8s, Helm, RBAC, HPA | ₹0 |
| 3 — AWS + Terraform | 13–22 | Terraform, EKS, RDS, Lambda, SQS, SNS | ₹3,000–4,000 |
| 4 — CI/CD | 23–30 | GitHub Actions, ArgoCD, GitOps | ₹2,000–3,000 |
| 5 — Observability | 31–37 | Prometheus, Grafana, Istio | ₹2,000–3,000 |
| 6 — Security | 38–45 | OPA, Falco, Vault | ₹2,000–3,000 |
| 7 — Polish | 46–50 | Demo, docs, resume | ₹1,000–2,000 |
| **Total** | **50 days** | **Everything** | **₹10,000–15,000** |

