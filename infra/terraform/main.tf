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
    "storage.googleapis.com",
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
# The key VALUE is never in git. Either Terraform adds the version from TF_VAR_openrouter_api_key
# (infra/deploy.sh does; the value is then in the local, gitignored state), or leave the variable empty
# and add it out-of-band:  printf '%s' "$OPENROUTER_API_KEY" | gcloud secrets versions add openrouter-api-key --data-file=-
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

# --- state that must outlive an instance -------------------------------------------------
# Connectors added in the web UI and conversation sessions used to live on the instance's
# in-memory disk, so every scale-to-zero, crash or deploy silently deleted them. They are small
# files, written by one instance (max_instance_count = 1), so a Cloud Storage FUSE mount is enough.
# The vector index stays on local disk: it is derived data and start-up re-syncs it from these files.
resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-${var.service_name}-state"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "runtime_state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# --- compute -------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  # The provider defaults this to true, which makes `terraform destroy` fail on the service. A demo must be
  # removable; for production, set it true and remove the service deliberately.
  deletion_protection = false

  template {
    service_account       = google_service_account.runtime.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2" # required for Cloud Storage volume mounts
    scaling {
      min_instance_count = 0
      max_instance_count = 1 # one writer for the state bucket; each instance keeps its own local index
    }
    volumes {
      name = "state"
      gcs {
        bucket        = google_storage_bucket.state.name
        read_only     = false
        mount_options = ["uid=1000", "gid=1000", "implicit-dirs"] # the image runs as `app` (uid 1000)
      }
    }
    containers {
      image = var.image
      ports { container_port = 8080 }
      volume_mounts {
        name       = "state"
        mount_path = "/state"
      }
      env {
        name  = "CONNECTORS_DIR"
        value = "/state/connectors"
      }
      env {
        name  = "SESSION_DIR"
        value = "/state/sessions"
      }
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
  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.runtime_reads_key, google_storage_bucket_iam_member.runtime_state]

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
output "state_bucket" { value = google_storage_bucket.state.name }

# Optional: let Terraform manage the secret version when the key is supplied as a
# sensitive variable (TF_VAR_openrouter_api_key). Leave it empty to add versions with
# `gcloud secrets versions add` instead and keep the value out of Terraform state.
resource "google_secret_manager_secret_version" "openrouter" {
  count       = var.openrouter_api_key == "" ? 0 : 1
  secret      = google_secret_manager_secret.openrouter.id
  secret_data = var.openrouter_api_key
}
