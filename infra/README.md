# infra

Everything needed to run OmniMed locally, on any Kubernetes cluster, or on AWS.

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Full local stack |
| `postgres/` | Image with pgvector; `init/` holds the schema, reference vocabularies and synthetic demo data |
| `keycloak/` | Image with the `omnimed` realm: roles, clients and persona accounts |
| `traefik/routes.yml` | Gateway routes, rate limiting and security headers |
| `k8s/` | Kustomize base, components and overlays |
| `terraform/` | AWS production infrastructure |

## Local stack

| Service | Host port | Notes |
|---|---|---|
| web | 3000 | Persona portals |
| traefik | 8000, 8443 | API gateway: `/api/clinical`, `/api/diag`, `/api/life`, `/api/ai` |
| keycloak | 8080 | Admin `admin` / `admin` |
| postgres | 5432 | `omnimed` / `omnimed` |
| minio | 9000, 9001 | Console on 9001, `omnimed` / `omnimed-secret` |
| vault | 8200 | Dev mode, token `root`, in-memory |
| nats | 4222, 8222 | JetStream; monitoring on 8222 |
| redis | 6379 | |
| orthanc, services | internal only | Reach the services through the gateway |

The `tests` service is in the `test` profile and only runs when asked for.

## Kubernetes

```text
k8s/
├── base/                   app deployments, services, HPAs, ingress, default-deny NetworkPolicies
├── components/platform/    NATS, Orthanc, Keycloak
├── components/local-data/  in-cluster Postgres, Redis, MinIO, Vault (dev only)
└── overlays/
    ├── dev/                1 replica, local data, host omnimed.localtest.me
    └── prod/               3 replicas, managed AWS data, secrets via External Secrets
```

The manifests reference `ghcr.io/omnimed/*:0.1.0` images, which are not published. Build and push your own, then
point the overlay at them:

```bash
R=<registry>   # for example ghcr.io/<your-user>
docker build -t $R/clinical:0.1.0 services/clinical
for s in diagnostics lifesciences ai; do docker build -f services/$s/Dockerfile -t $R/$s:0.1.0 services; done
docker build -t $R/web:0.1.0 apps/web
docker build -t $R/postgres:0.1.0 infra/postgres
docker build -t $R/keycloak:0.1.0 infra/keycloak
# push them, then in each overlay you use:
cd infra/k8s/overlays/dev
for s in clinical diagnostics lifesciences ai web postgres keycloak; do kustomize edit set image ghcr.io/omnimed/$s=$R/$s:0.1.0; done
```

The Python services build from `services/` because they share `services/common`. Validate the rendered manifests with:

```bash
kubectl kustomize infra/k8s/overlays/prod | kubeconform -strict -ignore-missing-schemas -summary
```

## Terraform (AWS)

Creates the VPC, EKS, RDS PostgreSQL, ElastiCache Redis, KMS keys, S3 buckets (audit bucket with Object Lock) and
Secrets Manager entries in `ap-south-1`.

```bash
cd infra/terraform
cp backend.hcl.example backend.hcl
terraform init -backend-config=backend.hcl
terraform plan
```

After applying, still to do by hand:

1. Create the `keycloak` database on RDS.
2. Install the External Secrets Operator and ingress-nginx, and set the role ARN from the `external_secrets_role_arn` output.
3. Replace the placeholder hostnames (`omnimed.example.org`, `auth.omnimed.example.org`) in the prod overlay.
4. Run Vault in HA with KMS auto-unseal and put its token in the app secret.
