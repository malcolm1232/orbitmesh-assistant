# Cloud deployment (GCP)

This document is the deployment write-up requested with the assignment.
Everything under **Implemented** exists in `infra/terraform/`, `cloudbuild.yaml` and `.github/workflows/deploy.yml` and has been applied once to a real project; everything under **Described only** is design.

## Compute: Cloud Run, and why

The assistant is a stateless request/response service with bursty, low average traffic and a cold-start budget of a few seconds (the ONNX embedding model and the corpus index load in ~2 s from the image).
That profile is exactly what Cloud Run is for:

- **Scale to zero.** A support bot for one product line idles most of the day. `min_instance_count = 0` means the demo costs nothing while nobody is talking to it; `max_instance_count = 1` keeps a single writer on the state bucket (production would move sessions to a database and raise the cap - see State below).
- **One image, one artefact.** The same `Dockerfile` that `docker compose` uses locally is what Cloud Run runs. The embedding model and the ingested index are baked at build time, so a container is fully functional with no network calls except to OpenRouter.
- **Concurrency without threads to manage.** Each turn is dominated by one LLM round-trip (1-5 s). Cloud Run's request concurrency (default 80) lets one small instance hold many idle-waiting conversations.
- **Managed TLS, IAM, probes, revisions and rollback** come for free, and the request/instance/latency metrics feed the dashboard without an agent.

Why not the alternatives:

- **GKE / Kubernetes** - real operational cost (cluster upgrades, node pools, ingress) for a workload with one container and no sidecars. It becomes right at the 100x point described in `DESIGN.md`, when a shared Qdrant cluster, an embedding service and async ingestion workers need to be co-scheduled.
- **Compute Engine VM** - cheapest steady-state, but no scale-to-zero, hand-rolled deploys, and patching.
- **Cloud Functions** - the 30 MB model plus the vector index make cold starts and package limits awkward; Cloud Run gives the same billing model with a proper container.

### State

The vector index is currently baked into the image (embedded Qdrant under `/data/qdrant`).
That is deliberate for a 4k-word corpus: a corpus change is a code change, goes through CI, and produces a new immutable revision - there is no runtime store to drift.
Connectors added through the UI and session state live in a Cloud Storage bucket (`<project>-orbitmesh-assistant-state`) mounted at `/state` with Cloud Storage FUSE (`CONNECTORS_DIR=/state/connectors`, `SESSION_DIR=/state/sessions`; gen2 execution environment, mounted as the image's uid 1000).
They used to live on the instance's in-memory disk, so every scale-to-zero, crash or deploy deleted them.
The vector index stays on local disk because it is derived data: on start-up the fingerprint of the bucket's connectors differs from the baked index and `sync_all()` re-embeds them (verified by forcing a new revision: the Drive node came back with the same 33 chunks).
The compose stack persists the same directories in the `app_data` volume.
FUSE has no file locking, which is why the service stays at one instance.
At real load, sessions move to Memorystore (Redis) or Firestore with a TTL, keyed by `session_id` (see "Described only").

## CI/CD pipeline

Implemented (GitHub Actions + Cloud Build; see the two workflow files and `cloudbuild.yaml`):

| stage | what runs | where |
|---|---|---|
| **test** | `make test` (81 tests: chunking, idempotent re-ingest, retrieval isolation, guardrails, memory, JSONL contract) with the hashing embedder and mock LLM - no credentials, no network | `ci.yml` job `tests`, also the first Cloud Build step |
| **ingest + retrieval eval** | ingest into an ephemeral Qdrant service container twice (second run must report `stale deleted=0`), then `make eval` with the real local embedding model and the mock LLM; results uploaded as an artifact | `ci.yml` job `retrieval-eval`, triggered by changes to `corpus/**` or the ingestion/retrieval modules |
| **build** | `docker build` of the single image; the corpus is ingested during the build so a broken corpus fails the build, not the deploy | `deploy.yml` / `cloudbuild.yaml` |
| **push** | Artifact Registry `asia-southeast1-docker.pkg.dev/<project>/orbitmesh/assistant:<git sha>` | same |
| **deploy** | `gcloud run deploy` of the new tag; Cloud Run does a rolling revision switch with the startup probe gating traffic | same |
| **smoke** | `GET /health` must return 200 and `POST /chat` must return a valid action | `deploy.yml` last step |

GitHub authenticates to GCP with **Workload Identity Federation** (OIDC), so no service-account key is stored in GitHub.
Infrastructure changes go through `terraform plan` in a PR and `terraform apply` on merge (described; the apply in this submission was run from a workstation).

Described only: a `staging` Cloud Run service receiving every `main` build with a canary of the private eval suite run against it, and promotion to `prod` by tagging, with Cloud Run traffic splitting (e.g. 10% for 15 minutes with the alert policies below as the automatic rollback trigger).

## Secrets and configuration

- The only secret is the OpenRouter key. It lives in **Secret Manager** (`openrouter-api-key`), mounted into the container as an environment variable by Cloud Run at start-up (`value_source.secret_key_ref`, version `latest`). The runtime service account has `secretmanager.secretAccessor` on that one secret and nothing else.
- The value never touches git. Terraform creates the secret; the version is added either out-of-band (`gcloud secrets versions add ... --data-file=-`) or, as in this submission, by Terraform from the sensitive variable `TF_VAR_openrouter_api_key` (then it is in the local, gitignored state file - use the GCS backend with CMEK for a shared team state, or the out-of-band path).
- Rotation: add a new version, redeploy (or restart) the service; old versions are disabled, not deleted, so a rollback still works. Cloud Run pins `latest` at instance start, so rotation is a revision roll, which is also what the alert on `orbitmesh_llm_errors_total{reason=AuthenticationError}` would catch if a key was revoked.
- Non-secret configuration (model id, retrieval knobs, log level) are plain environment variables set in the Terraform service definition, so a config change is a reviewed diff and a new revision.
- Locally the same variables come from `.env` (gitignored, `.env.example` documented); `LLM_PROVIDER=mock` is the no-credentials mode used by CI.

## Networking

- Cloud Run gives the service an HTTPS endpoint with a managed certificate. Ingress is `INGRESS_TRAFFIC_ALL` and the invoker role is granted to `allUsers` **for the demo only** (`allow_unauthenticated = true`).
- For customer traffic the intended shape is: an external HTTPS load balancer in front of the Cloud Run service with **Cloud Armor** (rate limiting per IP, WAF rules) and the service itself set to `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` so it cannot be hit directly; authentication via the product's existing customer identity (an ID token checked at the edge) or Identity-Aware Proxy for an internal support-agent console.
- Egress is only to `openrouter.ai` over HTTPS. No VPC connector is needed today. If the vector store moves to a managed Qdrant cluster or Memorystore, the service gets a Serverless VPC connector / Direct VPC egress and those stores sit on private IPs with no public endpoint.
- The service account is dedicated (`orbitmesh-assistant-run@…`) with no project-level roles, only the secret binding; the Cloud Run agent writes logs and metrics through its default permissions.

## What was actually deployed

- `terraform apply` in `infra/terraform/` against a real project created the Artifact Registry repo, the secret, the runtime service account and its IAM binding, the Cloud Run service, the uptime check, four alert policies, four log-based metrics and the dashboard.
- The image was built and pushed with Cloud Build (`gcloud builds submit --tag …`).
- The live URL is printed by `terraform output service_url`; the web UI (Ask, Connectors with an upload connector created and a revised document indexed, Dashboard), `/health`, `/chat`, `/api/*` and `/metrics` were verified in a browser and with `curl` (see the README for the exact commands).

## Cost

At the demo's traffic the bill rounds to zero: Cloud Run bills only while a request is in flight, Artifact Registry stores one ~800 MB image, and the alerting/dashboard resources are free tier.
The variable cost is the LLM: ~$0.0007 per turn (measured average over 172 development calls) with `openai/gpt-4.1-mini` (measured by OpenRouter's `usage.cost` and exported as the `orbitmesh_llm_cost_usd_total` metric), so 10k conversations of 4 turns is about $36.
