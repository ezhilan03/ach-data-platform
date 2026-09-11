output "registry_url" {
  description = "Docker registry prefix to push the runtime image to."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "lake_bucket" {
  description = "GCS bucket backing the raw zone. Set ACH_LAKE_ROOT to gs://<this> to run against it."
  value       = google_storage_bucket.lake.name
}

output "lake_uri" {
  description = "Value for ACH_LAKE_ROOT when running against GCS instead of a local path."
  value       = "gs://${google_storage_bucket.lake.name}"
}

output "runtime_service_account" {
  description = "Service account email the platform should run as."
  value       = google_service_account.runtime.email
}
