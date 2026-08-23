# Candidate selection - finding the probable replacements

Goal: a shortlist of 4-8 models worth the sweep. The field turns over monthly - new open-weight releases regularly leapfrog the incumbents and prices drop in steps. **Everything here is a procedure, not a list. Never assert current models, ranks, or prices from memory - fetch them.**

## Two leaderboards, crossed

Shortlist from the intersection of two live sources:

1. **Quality: Artificial Analysis Intelligence Index** (artificialanalysis.ai). Filter to open-weight models. Take the top ~5-8 by index score. This finds the capability frontier.
2. **Usage: OpenRouter rankings** (openrouter.ai/rankings). Take the top open-weight models by token volume. This finds what the market actually runs at scale - high usage means battle-tested serving, stable providers, and real-world price/quality fit that a lab benchmark misses.

Why both: quality-only shortlists include models nobody can serve reliably; usage-only shortlists include cheap models with quality cliffs. The reference experiment found both failure modes - a benchmark #1 that lost on the actual task, and a usage #1 that was significantly the worst writer.

Then verify each shortlisted model exists on OpenRouter and pull its live pricing via the API (`templates/pick_candidates.py` does this - `GET /api/v1/models`, open-weight models are the ones with a `hugging_face_id`).

## Composition rules for the shortlist

- **Span the price range.** Include at least one ultra-cheap model (10-50x below the frontier) - on structured/rubric tasks they are sometimes indistinguishable from models 10x their price, and that is the biggest savings if it holds.
- **Include the current price/quality sweet spot** (usually mid-price recent releases).
- **Include the incumbent frontier model** as the baseline - it goes through the same sweep.
- 4-8 candidates total. Below 4 you learn too little; above 8 the judge panel's pairwise comparisons get noisy and expensive.

## Task-type priors (weak priors - the sweep decides)

Use these only to make sure the right FAMILIES are represented, never to pre-declare a winner:

- **Structured / rubric-following (scoring, extraction, classification):** broadly solved across the open field - even ultra-cheap models often go perfect. Expect to pick on price and latency, not accuracy. Make sure the cheapest tier is represented.
- **Customer-facing copy / voice:** the differentiator is honesty (fabricated metrics) and voice adherence, not fluency - everyone is fluent now. Expect a statistical tie at the top; pick on fabrication rate, cost, speed.
- **Vision / OCR / browser-agent tasks:** Qwen-family open models have a strong track record here (and run well locally via Ollama) - ensure one is on the list.
- **Code / tool-use:** coding-specialist variants can beat their own family's generalist flagship on non-coding tasks too - a specialist label is not a disqualifier.
- **Long-context:** check the context window on the model listing before shortlisting; it varies 10x across the open field.

Benchmark rank != task fit. The reference experiment's AA-index #1 did not win a single task.

## Provider pinning - build the pin map at shortlist time

OpenRouter load-balances each model across multiple backend providers serving **different quantizations** (fp4/int4 vs fp8/bf16). Unpinned, consecutive calls to "the same model" hit different weights - see `rigor.md` #3.

For each candidate (`templates/pick_candidates.py` automates this):

1. `GET /api/v1/models/{author}/{slug}/endpoints` - lists every provider serving the model with its quantization.
2. Rank precision: fp32 > bf16/fp16 > fp8 > int8 > fp4/int4 > unstated (treat unstated as low).
3. Pin the highest-precision provider; tie-break by lower price, then by provider uptime if visible.
4. Record `{model: {provider, quant}}` in `provider_pins.json`; the sweep sends `"provider": {"order": [pin], "allow_fallbacks": false}` on every call.
5. Verify in the sweep output that the served provider matches the pin (the response reports who actually served it).

Closed frontier models (Anthropic/OpenAI/Google) need no pin - first-party serving is homogeneous.

## Practical gotchas (from the reference experiment)

- **Free-tier / low-credit keys distort results.** OpenRouter pre-authorizes worst-case cost (max_tokens x output price), so pricier models 402 while cheap ones pass - which silently biases the sweep toward cheap models. Load ~$10 of credit before sweeping; the whole reference eval cost ~$1.15.
- **max_tokens is a trap in both directions.** Too low starves heavy reasoners into silent empty outputs; the pre-authorization means too high can 402 on a thin account. Generous max_tokens + real credit is the safe zone.
- **Runaway reasoners exist.** Some models burn thousands of reasoning tokens and time out or return empty. Count empties as failures in grading; also note them as an operational cost in the report.
- Pinned providers can be down or rate-limited; the sweep retries with backoff, but if a pin is persistently unavailable, re-pin to the next-highest precision and note it in the report.
