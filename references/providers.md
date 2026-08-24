# Providers - when to route through OpenRouter vs. call a provider directly

Two provider modes, declared per model in `tasks.py`'s `MODELS`:

- `"openrouter"` (default) - the original methodology, unchanged. One API, 400+ models, live
  pricing/endpoint discovery via `pick_candidates.py`. Use this for *discovering* and *shortlisting*
  candidates - OpenRouter's breadth is exactly what candidate-selection.md's "cross two live
  leaderboards" step needs.
- `"openai_responses"` - calls `https://api.openai.com/v1/responses` directly, with `strict: true`
  JSON-schema structured output. Use this when the task you're evaluating actually runs this way in
  production and OpenRouter can't reproduce the exact calling shape (this methodology hit that gap
  directly: a production gate that calls the Responses API with `strict` JSON-schema mode has no
  equivalent OpenRouter chat-completions request that tests the same thing).

## Why this matters for the verdict, not just the plumbing

An eval that benchmarks candidates through OpenRouter but validates the winner against a different
calling pattern than production actually uses is answering a different question than "will this
work in production." If production calls a provider directly, the sweep that matters is the one
that also calls it directly - OpenRouter is for *finding* the candidate, not for *proving* it.

## Adding a new direct provider

Each template that dispatches calls (`run_sweep.py`, `build_golden.py`) defines its own
`call_openai_responses`-shaped function and a small dispatcher keyed on `model_cfg["provider"]` -
duplicated per file, matching this codebase's own convention (see `run_sweep.py`'s `call_openrouter`
next to `call_openai_responses`). Adding e.g. a direct Anthropic Messages-API path means adding
`call_anthropic_direct(...)` next to the existing two, and one more `if provider == ...` branch -
no shared package, no new import structure. Do this only when a real task needs it, the same way
`openai_responses` support was added here because `LifeOS/practical`'s gate needed it, not
speculatively.

## What does NOT change per provider

The statistical methodology (seeds, case-clustered stats, tied-vs-worse framing), the golden-set
discipline (human validation, hard/trap cases), `preflight.py`, `build_report.py`. Only how a cell's
raw model call is made, and whether `grade.py` needs to parse text or can trust an already-structured
result (`content_json`), differ by provider.

## Known limitations

`preflight.py estimate()` does not yet support `provider: "openai_responses"` models (it will crash
on a dict-shaped MODELS entry) - only `preflight.py validate()` is provider-aware today; a future
pass should extend `estimate()` similarly.
