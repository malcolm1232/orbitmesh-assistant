#!/usr/bin/env bash
# Remove everything infra/deploy.sh created: Cloud Run service, state bucket (UI connectors and
# sessions included), image registry and images, secret, service account, monitoring.
# APIs stay enabled (disable_on_destroy = false).
#
#   infra/destroy.sh PROJECT_ID [REGION]          # shows the plan and asks
#   YES=1 infra/destroy.sh PROJECT_ID [REGION]    # no prompt
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${1:?usage: infra/destroy.sh PROJECT_ID [REGION]}"
REGION="${2:-asia-southeast1}"
export TF_VAR_project_id="$PROJECT" TF_VAR_region="$REGION" TF_VAR_image="unused-on-destroy"
TF=(terraform -chdir=infra/terraform)
APPROVE=(); [ "${YES:-}" = 1 ] && APPROVE=(-auto-approve)

"${TF[@]}" init -input=false
# A service created before deletion_protection = false was in main.tf still carries the provider's
# default (true), which makes destroy fail: switch it off first. Only touch a service that exists.
if "${TF[@]}" state list 2>/dev/null | grep -qx 'google_cloud_run_v2_service.app'; then
  "${TF[@]}" apply -input=false "${APPROVE[@]}" -target=google_cloud_run_v2_service.app
fi
"${TF[@]}" destroy -input=false "${APPROVE[@]}"
