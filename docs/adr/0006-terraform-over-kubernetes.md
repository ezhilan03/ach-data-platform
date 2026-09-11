# ADR-0006: Terraform for the deployment target; no Kubernetes

**Status:** accepted · 2026-09-11

## Context

The platform needs somewhere for CD to publish to, and the project would
benefit from infrastructure-as-code. Kubernetes was the other candidate.

## Decision

Terraform provisions the GCP resources the pipeline actually needs: an Artifact
Registry repository with immutable tags and a cleanup policy, a versioned GCS
bucket for the raw zone with lifecycle rules, and a service account with
resource-scoped IAM and no project-level roles. CI runs `fmt -check`,
`init -backend=false` and `validate` on every pull request.

No Kubernetes. The official Airflow Helm chart is documented as the next step.

## Why Terraform

It gives CD a real target and the resources are ones this repo uses. The
choices in it are the reviewable part: immutable tags mean a tag can never be
repointed at different bytes; `uniform_bucket_level_access` plus
`public_access_prevention = "enforced"` closes the two ways a bucket normally
ends up public; versioning plus lifecycle rules make a bad pipeline run that
overwrites a partition recoverable; enabling APIs in code kills the "it worked
on my project" class of bug, which is almost always an API someone clicked on
months ago.

The retention variable is documented with the reason a real ACH archive needs
one — NACHA requires originators to retain records for six years — because that
number belongs next to the bucket, not in someone's memory.

## Why not Kubernetes

Running kind, Airflow (webserver, scheduler, DAG processor, triggerer) and the
monitoring stack together on a 16 GB laptop leaves little room to actually run
pipelines. The deliverable would be several hundred lines of YAML demonstrating
the Helm chart's defaults, not a design decision.

There is also a weaker version of the same point: the CD pipeline here
publishes an image. Kubernetes would not make that better. Terraform does,
because the registry it pushes to is a resource Terraform creates.

## Why the deploy step stops at "published"

CD builds a signed, multi-arch image, pushes it to GHCR, and writes the digest
to the job summary. It does not deploy. A `gcloud run deploy` step that had
never run against a real project would be a stub that looks like it works,
which is worse for a reader than an explicit note saying what remains: GCP
Workload Identity Federation, then a deploy step targeting the registry and
service account this Terraform creates.

## Consequences

- `terraform apply` has never been run against a real project; only
  `fmt`/`validate` are exercised. Stated plainly in the README.
- Local state. A GCS backend is correct as soon as more than one machine runs
  this; the block is present and commented, because a committed backend
  pointing at a nonexistent bucket breaks `terraform init` for anyone cloning.
- Reading the lake from GCS needs DuckDB's `httpfs` extension and credentials;
  the `lake_uri` output is the value `ACH_LAKE_ROOT` would take.
