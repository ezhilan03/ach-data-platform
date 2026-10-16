# ADR-0007: The LLM triage step must beat a rules baseline

**Status:** accepted · 2026-09-11

## Context

The deterministic detector says *which* originator-days are anomalous. It does
not say what happened or what to do. That gap — turning a flagged cluster into
a classification, a root-cause hypothesis and a recommended action — is where a
language model earns its place, if it earns it at all.

## Decision

Every cluster is triaged twice.

**Rules baseline:** a lookup from dominant detector signal to incident type.
Six lines. Always runs, always produces a verdict.

**LLM:** receives an evidence packet — originator context, the flagged days'
metrics against baseline, the prior ten days, the return codes that arrived in
the window, the NACHA thresholds — and returns a pydantic-validated
classification, hypothesis, action, and the specific numbers it cited.

The model is **not told which signal fired**. The evaluation harness reports
both accuracies side by side.

## Why withhold the signal

Handing the model the detector's answer and then measuring whether it agrees
measures nothing. Withholding it makes the comparison meaningful: the rules
baseline gets the signal for free, so it is the bar. If the model cannot match
a six-line lookup table while reading the raw numbers, then the narrative it
writes alongside its classification — the part nobody can grade automatically —
is not trustworthy either.

This is a cheap, honest test for "is the model adding anything," and it is
enforced by a test (`test_evidence_packet_hides_signal_names`) rather than by
intention.

## Why there is no confidence field

The previous project in this portfolio measured self-reported LM confidence at
**0.71 when correct and 0.95 when incorrect** — inverted. A confidence number
in the schema would be a decoration that invites someone downstream to act on
it, so the field does not exist. What is collected instead: whether the output
passed schema validation, on which attempt, and whether it matched ground truth.

## Why triage failure does not fail the DAG

The rules baseline is deterministic and always produces a verdict. An Ollama
that is not running therefore degrades the output rather than reddening the
pipeline. Failures are counted per cluster, surfaced as
`ach_eval_triage_failures`, and reported by `check_eval.py` — swallowed
silently nowhere.

This is the same deterministic-first principle as the detector, applied to
availability instead of cost: the parts that must always work are the parts
that do not call a model.

## Provider strategy

All model calls go through `litellm`; the provider is the prefix of the
`TRIAGE_MODEL` environment variable. Local Ollama for development and demos
(free, no rate limits, nothing leaves the machine — a real selling point in
fintech), a hosted API for a deployed instance, same code path.

Output is requested as JSON, extracted tolerantly (reasoning tags and code
fences stripped, outermost object taken), validated with pydantic, and retried
once with the validation error in the prompt. `finish_reason == "length"` is
treated as a failure rather than parsed — a truncated response that happens to
contain valid JSON is not a valid answer.

## Measured outcome (2026-10-06)

`ollama_chat/qwen3:4b`, local, on an M4 Air. 11 clusters, 10 usable results,
76 minutes of model time.

| | rules baseline | LLM |
| --- | --- | --- |
| Accuracy on true incidents | **1.00** (9/9) | **0.62** (5/8) |
| Called a false positive "no_incident" | 0 / 2 (cannot) | **0 / 2** |
| Calls with no usable result | 0 | 1 |
| Cost per cluster | microseconds | ~7.6 min |

The model lost on the comparison it was given a fair shot at, and tied — at
zero — on the one the baseline was structurally incapable of. It collapsed
toward a single label: five of ten predictions were `nsf_spike`, including a
volume drop, a settlement delay, and both false-positive clusters.

Two mechanical findings worth keeping:

**Every successful call took two attempts.** The first response failed pydantic
validation every single time; the retry carrying the validation error
succeeded every single time. A schema-validated retry loop was not a safety
net here, it was load-bearing.

**Disabling the reasoning trace may have cost accuracy.** An earlier partial
run with thinking enabled got 3 of 3 correct, including a `volume_drop` that
this run — same cluster, `/no_think` set — called `nsf_spike`. n=3 against
n=8 is far too small to conclude from, but it is the obvious next thing to
measure, and the switch is now `TRIAGE_DISABLE_THINKING` rather than a patch.

### What this means for the pipeline

The decision this ADR pre-committed to is now live: **this step should ship as
the rules classification plus a templated summary**, with the model behind a
flag and off by default for the local deployment. The architecture does not
change — the evidence packet, the schema, the retry loop and the eval all stay,
because they are what made the negative result legible. What changes is the
default, and the README now says so in numbers rather than in hope.

The result is model-and-hardware specific, not a claim about LLM triage in
general. A hosted frontier model on the same packets may well clear the bar;
the harness is sitting there to find out, and the provider is one environment
variable. What has been measured is narrower and more useful than a verdict:
*a 4B local model on consumer hardware cannot do this job*, which is exactly
the claim the "nothing leaves the machine" selling point depends on.

## Consequences

- Triage quality depends on the model; the number is reported per model in the
  eval output, not baked into the README as a property of the system.
- CI runs rules-only. Adding a hosted model there would make the build depend
  on a third party's availability and bill.
- If the LLM consistently loses to the baseline, the honest conclusion is that
  this step should ship as rules plus a templated summary. The harness exists
  to make that conclusion available rather than avoidable.
