variable "project_id" {
  description = "GCP project that owns the registry, bucket and service account."
  type        = string
}

variable "region" {
  description = "Region for the Artifact Registry repository and the lake bucket."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix for every created resource, so one project can host more than one environment."
  type        = string
  default     = "ach-platform"
}

variable "lake_retention_days" {
  description = <<-EOT
    Days to keep raw Parquet in the archive bucket before deletion.
    The synthetic lake has no compliance requirement, but the knob is here
    because a real ACH archive does: NACHA requires originators to retain
    records for six years, and that number belongs in code next to the bucket
    rather than in someone's memory.
  EOT
  type        = number
  default     = 90
}

variable "image_retention_count" {
  description = "Number of recent image versions to keep in Artifact Registry."
  type        = number
  default     = 20
}

variable "labels" {
  description = "Labels applied to every resource that supports them."
  type        = map(string)
  default = {
    application = "ach-data-platform"
    managed-by  = "terraform"
  }
}
