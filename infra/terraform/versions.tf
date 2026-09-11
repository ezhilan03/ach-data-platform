terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Local state on purpose. A GCS remote backend is the right answer the
  # moment more than one person or one machine runs this, and switching is a
  # backend block plus `terraform init -migrate-state` -- but a committed
  # backend pointing at a bucket that does not exist is worse than no backend,
  # because it breaks `terraform init` for anyone cloning the repo.
  #
  # backend "gcs" {
  #   bucket = "ach-platform-tfstate"
  #   prefix = "platform"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
