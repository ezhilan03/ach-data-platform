# Deployment target for the platform: a private image registry, an archive
# bucket for the raw lake, and the least-privileged identity that runs it.
#
# Scope is deliberately small. This is the infrastructure the CD pipeline
# actually needs in order to have somewhere to push and something to read --
# not a speculative VPC, GKE cluster and Cloud SQL instance that nothing in
# this repo uses. Infrastructure that is not exercised by a deploy is
# decoration, and it rots.

locals {
  registry_id = "${var.name_prefix}-images"
  bucket_name = "${var.name_prefix}-lake-${var.project_id}"
}

# Enabling APIs in code matters more than it looks: "it worked on my project"
# is almost always an API someone clicked on months ago and forgot.
resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  # Leave APIs enabled on destroy -- disabling a project-wide API because one
  # stack was torn down is a great way to break something unrelated.
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = local.registry_id
  description   = "Container images for the ACH data platform (Airflow + dbt runtime)."
  format        = "DOCKER"
  labels        = var.labels

  docker_config {
    immutable_tags = true # a tag can never be repointed at different bytes
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.image_retention_count
    }
  }

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s" # 30 days
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "lake" {
  project  = var.project_id
  name     = local.bucket_name
  location = var.region
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Versioning plus a retention rule is the cheap insurance for an append-only
  # archive: a bad pipeline run that overwrites a partition is recoverable.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = var.lake_retention_days
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age                = 30
      with_state         = "ARCHIVED"
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }

  # Parquet is already compressed and read far more often than written.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  depends_on = [google_project_service.required]
}

# The identity the platform runs as. No project-level roles: it can read the
# images it is told to run and read/write only its own bucket.
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-runtime"
  display_name = "ACH data platform runtime"
  description  = "Runs the containerised pipeline; reads images, reads and writes the lake bucket."
}

resource "google_artifact_registry_repository_iam_member" "runtime_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_lake_writer" {
  bucket = google_storage_bucket.lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}
