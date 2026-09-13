terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
  # Remote state: create the bucket once (see DEPLOYMENT.md) and uncomment.
  # backend "gcs" { bucket = "orbitmesh-tfstate", prefix = "assistant" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
