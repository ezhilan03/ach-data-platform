# ADR-0008: DAG files import from the installed package, never from each other

**Status:** accepted · 2026-09-21 · found by verification, not by design

## Context

The three DAGs share asset definitions. The obvious place for them is a
sibling module, `airflow/dags/assets.py`, imported as `from assets import ...`.
That is a common layout and it is what this project shipped with.

It does not work. Airflow 3 does not put the DAGs folder on `sys.path`, so the
DAG processor raises `ModuleNotFoundError: No module named 'assets'` for every
file that imports it. All three DAGs failed to import. Nothing would have run.

Worse, the failure is quiet by design: a DAG that fails to import does not
crash the scheduler, it simply stops being scheduled, and the only signal is
the `dag_processing.import_errors` counter — which this project alerts on
precisely because that is such a poor way to find out.

## How it was missed, and then found

`scripts/parse_dags.py` originally imported each DAG file by hand, and did
`sys.path.insert(0, dags_dir)` first so that sibling imports would resolve.
That one line made the check more permissive than production: it reported a
clean parse of three files that the real processor could not load.

A verification tool that is more permissive than production is worse than no
tool, because it converts an unknown into a false assurance. The script now
uses Airflow's own `DagBag` against the configured `dags_folder` and asserts
both "zero import errors" and "at least one DAG found" — the second condition
matters because the original script's DAG-detection was also wrong, reporting
"no DAG defined" for files that define DAGs via an unassigned `@dag()` call.

## Decision

Anything more than one DAG file needs lives in the installed package.
`ach_platform.assets` holds the asset definitions; DAG files import from
`ach_platform.*` and contain only wiring.

`ach_platform/__init__.py` does not import the assets module, so the package
stays importable in CI and on a laptop with no Airflow installed. Only that
one submodule requires Airflow.

## Consequences

- Changing an asset definition means rebuilding the image rather than editing
  a bind-mounted file. Correct: assets are a contract between DAGs, and a
  contract that can be edited without a deploy is not one.
- DAG files stay thin, which was already the intent — this makes it structural
  instead of aspirational.
- The rule generalises: a DAG file may import from `ach_platform`, from
  Airflow, and from the standard library. Never from a sibling.
