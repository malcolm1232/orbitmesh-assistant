# OrbitMesh Support Assistant on Cloud Run (GCP).
#
#   APIs -> Artifact Registry -> Secret Manager (OpenRouter key) -> service account
#   -> Cloud Run v2 service (scale-to-zero) -> Monitoring: uptime check, alert policies, dashboard.
#
# The image is built by Cloud Build (cloudbuild.yaml) or the GitHub Actions deploy workflow and
# passed in as var.image; Terraform owns everything around it.

locals {
  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "orbitmesh"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# --- secrets -------------------------------------------------------------------------
# The key VALUE is never in Terraform state or git: add a version out-of-band
#   printf '%s' "$OPENROUTER_API_KEY" | gcloud secrets versions add openrouter-api-key --data-file=-
resource "google_secret_manager_secret" "openrouter" {
  secret_id = "openrouter-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# --- identity ------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "${var.service_name}-run"
  display_name = "OrbitMesh assistant runtime"
}

resource "google_secret_manager_secret_iam_member" "runtime_reads_key" {
  secret_id = google_secret_manager_secret.openrouter.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# --- compute -------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 1   # demo: UI-added connectors + sessions live on the instance disk (see DEPLOYMENT.md)
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      env {
        name  = "LLM_MODEL"
        value = var.llm_model
      }
      env {
        name  = "LLM_PROVIDER"
        value = "openrouter"
      }
      env {
        name  = "APP_VERSION"
        value = var.image
      }
      env {
        name = "OPENROUTER_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openrouter.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
      }
      liveness_probe {
        http_get { path = "/health" }
        period_seconds = 30
      }
    }
  }
  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.runtime_reads_key]

  # CI rolls new image tags with `gcloud run deploy` (deploy.yml / cloudbuild.yaml); Terraform
  # owns everything else about the service and must not revert those rollouts.
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "service_url" { value = google_cloud_run_v2_service.app.uri }
output "runtime_service_account" { value = google_service_account.runtime.email }

# Optional: let Terraform manage the secret version when the key is supplied as a
# sensitive variable (TF_VAR_openrouter_api_key). Leave it empty to add versions with
# `gcloud secrets versions add` instead and keep the value out of Terraform state.
resource "google_secret_manager_secret_version" "openrouter" {
  count       = var.openrouter_api_key == "" ? 0 : 1
  secret      = google_secret_manager_secret.openrouter.id
  secret_data = var.openrouter_api_key
}
