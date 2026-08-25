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

**Caveat - this is a simplified SINGLE-OFFER variant, not a byte-for-byte
mirror of the production call.** `gate.mjs`'s current Responses API call is
BATCHED: one call evaluates several offers at once and returns a
`decisions: [...]` array. This example models one offer per call with a flat
decision schema, which is the same decision logic in an easier-to-grade shape.
So it demonstrates the methodology (direct-provider sweep, golden set, honest
tradeoff table) faithfully; it does not measure the exact batched request shape
production sends. Re-transcribing the batch shape is deferred deliberately - it
needs fresh verification against `gate.mjs`, which is still being developed.
If the batching itself is what you need to evaluate (it changes token counts,
latency, and per-offer error coupling), re-transcribe before trusting the
numbers.

## Scope

Only cases where `GATE_MODEL` itself is asked to decide. Self-exclusion and
pure-tier-eligibility denials are resolved in code before any model call in
the real system (`evaluateOffer()`'s hard preconditions) - there's no model
judgment to grade there, so they're intentionally not in this golden set.

## Before running this for real

1. `python3 preflight.py validate` from this directory - free, no key needed
   (`preflight.py` is already copied in here alongside `tasks.py`; the
   templates always resolve `from tasks import ...` against their own
   directory).
2. Fill in at least one real candidate in `MODELS` (see the `TODO` in
   `tasks.py`) - both this frontier and any candidate need `provider:
   "openai_responses"` for an apples-to-apples comparison against the real
   calling pattern.
3. A human (not the agent that wrote this file) reviews each case's `note`
   against the proposed answer and marks it `"validated": true` before any
   verdict here is trustworthy - the whole methodology refuses to grade an
   unvalidated reference.
4. Copy the rest of the templates in next to this `tasks.py`
   (`cp ../../templates/*.py .`), then:

   ```bash
   OPENAI_API_KEY=<key> python3 build_golden.py    # then run_sweep.py, grade.py, build_report.py
   ```

   `OR_KEY` is NOT needed here: every model in this example's `MODELS` uses
   provider `"openai_responses"`, and the templates demand only the keys the
   declared providers actually use.
5. Add a `PRICING` entry to `tasks.py` for every model before you read any
   savings number. OpenRouter reports `usage.cost` per call; the direct OpenAI
   Responses path does not, so `$/call` comes from `PRICING` (in $ per token,
   off OpenAI's rate card) or not at all. Without it, `grade.py` and
   `build_report.py` honestly print `n/a` for `$/call` and `saved` - they will
   never invent `$0.0000` or `100% saved`.
