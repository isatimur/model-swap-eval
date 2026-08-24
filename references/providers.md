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

The case-clustered statistics and tied-vs-worse framing, the golden-set discipline (human
validation, hard/trap cases), and `build_report.py`. Only how a cell's raw model call is made, and
whether `grade.py` needs to parse text or can trust an already-structured result (`content_json`),
differ by provider.

## Known limitations of the `openai_responses` path

Read these before quoting any number produced through it.

- **Seeds do not vary the request.** `call_openai_responses()` forwards neither `seed` nor
  `temperature` nor `max_tokens`. So N seeds issue N *identical* requests, and the per-seed spread
  measures only whatever nondeterminism the endpoint itself has - it is not the seed-variance noise
  floor rigor.md #1 asks for. Report it as such, and do not read a tight spread as stability.
  For the same reason, `build_golden.py`'s seed-disjointness protection (its "M7" comment: proposal
  seeds 9001+ vs the sweep's SEEDS, so the frontier cannot trivially match its own reference) holds
  only on the `openrouter` path. On this path the proposal call and the frontier's sweep cells are
  the same request.
- **Cost is not reported by the API, so it comes from `tasks.py`'s `PRICING` dict or not at all.**
  A model with no `PRICING` entry has UNKNOWN cost. `run_sweep.py` warns once per model,
  `grade.py` prints `n/a` for that model's `$/call` and `saved`, and `grade_agg.json` carries
  `null` - never `$0.0000`, never `100% saved`.
- **`preflight.py estimate` prices OpenRouter models only.** It lists and skips
  `openai_responses` models (they have no OpenRouter `/models` pricing) and exits with a clear
  message if that leaves nothing to price. `preflight.py validate` is fully provider-aware.
- **Providers cannot be mixed inside one `tasks.py`.** A case's `input` is a string prompt for
  `openrouter` and a dict payload for `openai_responses`; one set of cases cannot satisfy both, so
  `preflight.py validate` errors on a mixed `MODELS`. Run the two provider sets as two evals.
