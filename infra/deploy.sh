#!/usr/bin/env bash
# Deploy the assistant to Cloud Run from scratch (a fresh checkout or the submission zip).
#
#   infra/deploy.sh PROJECT_ID [REGION]          # asks before each terraform apply
#   YES=1 infra/deploy.sh PROJECT_ID [REGION]    # no prompts
#
# Needs gcloud (authenticated, with rights to create the resources), terraform >= 1.6, and
# OPENROUTER_API_KEY in the environment or in .env. Order matters on an empty project:
#   1. APIs, Artifact Registry and the secret + key version  (the service needs both to start)
#   2. build and push the image with Cloud Build             (the service needs an image to exist)
#   3. everything else: bucket, service account, Cloud Run, monitoring
#   4. smoke test /health and /chat on the printed URL
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${1:?usage: infra/deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-asia-southeast1}"
KEY="${OPENROUTER_API_KEY:-$(grep -s '^OPENROUTER_API_KEY=' .env | cut -d= -f2- || true)}"
[ -n "$KEY" ] || { echo "OPENROUTER_API_KEY is not set (environment or .env)" >&2; exit 1; }
TAG="$(git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d-%H%M%S)"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/orbitmesh/assistant:$TAG"

export TF_VAR_project_id="$PROJECT" TF_VAR_region="$REGION" TF_VAR_image="$IMAGE" TF_VAR_openrouter_api_key="$KEY"
TF=(terraform -chdir=infra/terraform)
APPROVE=(); [ "${YES:-}" = 1 ] && APPROVE=(-auto-approve)

echo "==> terraform init"
"${TF[@]}" init -input=false

echo "==> 1/4 APIs, image registry, OpenRouter secret"
"${TF[@]}" apply -input=false "${APPROVE[@]}" \
  -target=google_project_service.apis \
  -target=google_artifact_registry_repository.repo \
  -target=google_secret_manager_secret_version.openrouter

echo "==> 2/4 build and push $IMAGE"
gcloud builds submit --project "$PROJECT" --region "$REGION" --tag "$IMAGE" .

echo "==> 3/4 Cloud Run service, state bucket, service account, monitoring"
"${TF[@]}" apply -input=false "${APPROVE[@]}"

URL="$("${TF[@]}" output -raw service_url)"
echo "==> 4/4 smoke test $URL"
for attempt in 1 2 3 4 5 6; do
  curl -fsS "$URL/health" && break
  [ "$attempt" = 6 ] && { echo "health check failed" >&2; exit 1; }
  sleep 10
done
echo
curl -fsS -X POST "$URL/chat" -H 'content-type: application/json' \
  -d '{"session_id":"deploy-smoke","message":"My N1 is flashing amber"}' | grep -q '"action"'
echo "deployed: $URL"
