# Worked example: LifeOS/practical's offer gate

Evaluates whether a cheaper/alternative model can replace `gpt-5.4-mini` as
`gate.mjs`'s `GATE_MODEL` - the compliance decision that runs on every offer
opportunity in a live voice-agent conversation, on the critical path
(measured 1.1-1.4s in production).

## Provenance

`tasks.py`'s system prompt (`GATE_INSTRUCTIONS`), schema (`OFFER_GATE_SCHEMA`),
and the 5 golden-set cases are transcribed **read-only** from
`LifeOS/practical/gate.mjs` and its `AGENTS.md`, as of 2026-08-23. Running
this eval never modifies that project.

## Scope

Only cases where `GATE_MODEL` itself is asked to decide. Self-exclusion and
pure-tier-eligibility denials are resolved in code before any model call in
the real system (`evaluateOffer()`'s hard preconditions) - there's no model
judgment to grade there, so they're intentionally not in this golden set.

## Before running this for real

1. `python3 preflight.py validate` (copy `preflight.py` alongside this
   `tasks.py`, or run from `templates/` with `PYTHONPATH` pointed here) - free,
   no key needed.
2. Fill in at least one real candidate in `MODELS` (see the `TODO` in
   `tasks.py`) - both this frontier and any candidate need `provider:
   "openai_responses"` for an apples-to-apples comparison against the real
   calling pattern.
3. A human (not the agent that wrote this file) reviews each case's `note`
   against the proposed answer and marks it `"validated": true` before any
   verdict here is trustworthy - the whole methodology refuses to grade an
   unvalidated reference.
4. `OPENAI_API_KEY=<key> python3 build_golden.py` then `run_sweep.py`,
   `grade.py`, `build_report.py` - same order as the main methodology.
