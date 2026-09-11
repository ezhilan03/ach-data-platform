# Airflow image with the platform code and dbt baked in.
#
# Two Python environments on purpose (ADR-0004): Airflow and dbt are both
# large applications with opinions about Jinja2, click, protobuf and friends.
# Resolving them into one environment works until the day it doesn't, and the
# failure mode is a broken scheduler rather than a broken dbt run. dbt gets its
# own venv at /opt/dbt-venv and is invoked by absolute path via $DBT_BIN, so
# neither side can constrain the other. This is also how the Astronomer and
# Cosmos communities landed after the same fight.
FROM apache/airflow:3.3.1-python3.12

USER root

# build-essential is needed by a couple of wheels that have no aarch64 build
# (the primary build machine is an M-series Mac). Dropped from the final layer
# would be nicer, but a single-stage Airflow image keeps the entrypoint and
# the provider set intact, which matters more here than a few hundred MB.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# dbt in its own isolated environment.
RUN python -m venv /opt/dbt-venv \
    && /opt/dbt-venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/dbt-venv/bin/pip install --no-cache-dir "dbt-core>=1.10,<2" "dbt-duckdb>=1.9,<2" \
    && chown -R airflow:root /opt/dbt-venv

USER airflow

# Install the platform package into Airflow's environment. Only pyproject +
# src are copied, so a change to the dbt models or the dashboards doesn't
# invalidate this layer.
COPY --chown=airflow:root pyproject.toml README.md /opt/ach/
COPY --chown=airflow:root src /opt/ach/src
RUN pip install --no-cache-dir /opt/ach

ENV DBT_BIN=/opt/dbt-venv/bin/dbt \
    DBT_PROJECT_DIR=/opt/airflow/dbt/ach_dbt \
    PYTHONUNBUFFERED=1

# Sanity check at build time rather than at 3am: both environments import.
RUN python -c "import ach_platform, duckdb, litellm, prometheus_client" \
    && /opt/dbt-venv/bin/dbt --version
