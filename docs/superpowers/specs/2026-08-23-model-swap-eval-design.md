# model-swap-eval — design

**Status:** proposed
**Date:** 2026-08-23
**Name is a placeholder** — trivially renamed later; not a blocking decision.

## Context and motivation

`oss-migration-eval` (BayramAnnakov/oss-migration-eval) is a Claude Code skill for deciding,
with statistical rigor, whether a cheaper/open-weight model can replace an expensive one on a
recurring task. We've contributed three PRs upstream (bug fixes, preflight/report/CI tooling,
positioning docs) — all general-purpose value, staying in scope for that repo.

Competitive research this session found: nobody else combines "model migration decision" framing
with real statistical rigor. General eval frameworks (promptfoo, DeepEval, RAGAS) score output
quality, not migration decisions. A dedicated "Model Migration Playbook" article (tianpan.co,
2026-04) has zero mention of seeds, confidence intervals, or significance testing. promptfoo does
already have direct-provider plumbing (an `OpenAiResponsesProvider` with structured-output
support) — so that specific capability isn't a differentiator on its own, it's a prerequisite we
need and nobody bundles it with the rigor methodology.

**The gap oss-migration-eval itself has:** it only speaks OpenRouter. Real production systems
often call a provider directly (lower latency, no routing layer) and only reach for OpenRouter to
*discover* candidates — there's no path from "OpenRouter says candidate X wins" back to "does X
work through our actual direct-API calling pattern." We hit this concretely trying to pilot-eval
`swiirl-io/ai-worker`'s `entity_sentiment` agents (direct OpenAI, not OpenRouter).

**The forcing function:** `~/Dev/LifeOS/practical`'s `gate.mjs` — a real, running voice-agent
compliance gate that calls OpenAI's Responses API directly with `strict: true` JSON-schema output,
and already has an ad-hoc, manually-eyeballed rigor practice in its own `AGENTS.md` ("9 runs is a
small sample," "do not quote a rate from n=4"). It's a real production system with documented,
already-verified ground truth (a test matrix of guest scenarios → correct allow/deny decisions) —
a genuine dogfood case, not a synthetic one.

## Goals

1. Prove oss-migration-eval's rigor methodology (seeds, human-validated golden sets, honest
   tied-vs-worse statistics) against a real production system that requires direct-provider
   calling, not just OpenRouter-routable ones.
2. Do it without abandoning the thing that makes oss-migration-eval good: zero dependencies,
   "boring" single-file scripts, copy-paste-and-adapt rather than an installed framework.
3. Ship a real, credible worked example (the gate evaluation) built from already-verified
   production ground truth, as the flagship OSS proof point.

## Non-goals

- Not a general eval/observability platform (that's promptfoo/DeepEval/Langfuse's job).
- Not a provider-abstraction library aiming to support every vendor on day one — only what's
  needed for a real case (OpenAI Responses API now; others added the same way OpenRouter support
  was originally added, when a real need shows up).
- Not replacing the upstream oss-migration-eval PRs — those stay, and general-purpose fixes still
  land there first when they apply to both.

## Architecture

### Provider abstraction

Extract the existing OpenRouter call logic from `run_sweep.py`/`build_golden.py`/`judge.py` into
`providers/openrouter.py` (behavior unchanged — same body shape, same retry/backoff). Add
`providers/openai_responses.py`: POSTs to `https://api.openai.com/v1/responses` with
`instructions` + a JSON `input` payload + `text.format.json_schema` (`strict: true`), matching
`gate.mjs`'s exact call shape. Both expose the same return contract the templates already use
(content/cost/latency/finish_reason/etc.) so `run_sweep.py`'s cell loop doesn't need to know which
provider it's calling.

### `tasks.py` schema changes (breaking, deliberately — this is a fork, not bound to upstream compat)

- `MODELS` entries become `{"id": ..., "provider": "openrouter" | "openai_responses"}` (dict, not
  bare string). Default provider is `"openrouter"` for cases migrated straight from the original
  methodology.
- For `openai_responses` tasks: a case's `"input"` is a JSON-serializable dict (the actual payload
  shape sent to the Responses API), not a plain string. The task carries a `"json_schema"` field
  (the real strict schema) instead of relying on `grade.py`'s balanced-braces text-extraction —
  `strict: true` already guarantees valid, correctly-shaped JSON, so that parsing step is skipped
  entirely for this path (simplification, not just an addition).

### Grading

`grade.py`'s existing multi-field `ref_fields` exact-match already covers the gate's 4-field
output (`action`, `basis`, `reason`, `distress_present`) with no changes needed there. Only the
JSON-acquisition step (parse-from-text vs. already-structured) differs per provider.

### The LifeOS gate worked example

New task `offer_gate_decision`, `kind: structured`, `provider: openai_responses`, frontier =
`gpt-5.4-mini` (gate.mjs's real default). Golden-set cases drawn from `AGENTS.md`'s documented,
already-verified test matrix (disrupted guest + sit-down meal → allow/service_recovery, calm
guest asks about showers → allow/guest_asked, the reason-code-agreement bug case, etc.) — real
observed ground truth, not synthetic. Cases that never reach the model (self-exclusion and tier
checks are code-level preconditions in `gate.mjs`, not model decisions) are excluded — there's no
model decision to grade there. Validation is fast (confirming already-tested outcomes) but still
an explicit, logged step — not self-stamped.

### What carries over unchanged

All reference docs (`rigor.md`, `metrics.md`, `triage.md`, `candidate-selection.md`,
`optimization.md`, `deployment.md`), the statistical methodology, `preflight.py`, `build_report.py`,
`grade_agg.json`/`judge_agg.json` output shapes. `selftest.py` and `preflight.py` extend to cover
the new `provider` field and a fixture test for `call_openai_responses` (no real network — same
offline-fixture pattern already used for every other check in that suite).

## Testing strategy

Same discipline as oss-migration-eval's own `selftest.py`: every new code path gets an offline,
no-API-key regression check before it's trusted. Specifically:
- `providers/openai_responses.py`'s request-building logic, tested against a fixture matching
  `gate.mjs`'s real payload shape (no live call).
- `preflight.py validate` extended to catch a `provider: openai_responses` task missing
  `json_schema`, or a case whose `input` is a string where a dict is required.
- The gate worked example itself is validated with `preflight.py validate` before any real spend,
  same as every other tasks.py in this methodology.

## Risks / open questions

- **Repo location:** personal (`isatimur`) vs. `swiirl-io` org — a branding/comms call, not a
  technical one. Defaulting to personal unless told otherwise.
- **Cost of the first real run:** unknown until candidates are picked; `preflight.py estimate`
  (ported as-is) gives a live number before spending anything.
- **LifeOS is someone else's live project** (yours, but actively being worked on in parallel this
  session — its `AGENTS.md` changed mid-session already). The eval reads `AGENTS.md` and
  `gate.mjs` read-only; nothing in this plan modifies LifeOS itself.
