# Rigor - the honesty guardrails

These rules exist because a real experiment violated them, published-almost, red-teamed itself, and watched its headline reverse. Round 2 of that experiment declared a decisive winner: 9.00/10, swept every case, "clean generational win." Round 3 - same models, same cases, plus seeds, provider pins, and hard cases - dropped that winner to 7.00 +/- 2.0, mid-pack in a six-way statistical tie. Every rule below is a scar.

## 1. Single-seed LLM evals are noise

The killer stat: within-model spread across seeds/judges was +/- 1.92 points, while the ENTIRE between-model range (best to worst of 9 models) was 2.88 points. Per-model noise nearly equals the whole ranking. One sample per cell produced a false "9.00/10, swept all cases."

- Mandate **>= 3 seeds per model x case; default 5.**
- Every reported number carries mean +/- stderr (and 95% CI in the report).
- If budget forces a choice between more models and more seeds, cut models. Five models at 5 seeds beats ten models at 1 seed - the latter is a random-number generator with a bill.

## 2. You can identify the losers, not a winner

With overlapping CIs, "who's best" is unanswerable - but "who's clearly worse" usually is. In the reference run the top 6 were statistically indistinguishable while the bottom 2 separated cleanly (significantly below the pack).

- Compute pairwise z = (mean1 - mean2) / sqrt(se1^2 + se2^2); |z| > 1.96 separates at 95%.
- Report three buckets: **top cluster (statistically tied)**, mid, **clearly worse**.
- Never crown a winner off overlapping CIs. When the top is tied, say "tied on quality - pick on cost, speed, and deterministic failure rates" - that IS the finding, and it's a more useful one than a fake winner.
- **"Tied" = NOT SEPARABLE at this n = absence of evidence, NOT proof of equivalence.** At small n, only huge gaps are detectable, so everything short of catastrophic "ties." Always report the *minimum difference detectable at your n* (grade.py prints it). If that number is bigger than a gap you'd act on, your eval is underpowered - the fix is more **cases**, and until then you cannot say "model A = model B."
- **Aggregate at the CASE level, not the cell level.** At temperature 0 the per-seed repeats are near-replicates (some providers ignore the `seed` param entirely - the frontier especially), so N seeds x M cases is NOT N*M independent trials. Effective n = M cases; case difficulty is heterogeneous and usually one or two hard cases carry all the separation. Treating cells as iid Bernoulli trials inflates confidence and shrinks CIs artificially. grade.py clusters by case for exactly this reason.

## 3. Provider/quant routing silently corrupts results

OpenRouter load-balances each model across backends serving different quantizations. The reference experiment discovered its Round 2 data had silently served one flagship at int4/fp4 - it was benchmarking a lobotomized copy and attributing the result to the model.

This isn't a one-off scar - it's a live, recognized failure mode. A LessWrong writeup, ["Not Pinning Your OpenRouter Provider Might Invalidate Your Research"](https://www.lesswrong.com/posts/KsyoSAyBRXtwzSugg/not-pinning-your-openrouter-provider-might-invalidate-your), independently documents the same corruption path across other research codebases and argues most projects using OpenRouter don't pin at all. `pick_candidates.py` and `run_sweep.py` already build and enforce the fix this skill's own scar taught it to need.

- Pin every candidate to ONE highest-precision provider: `"provider": {"order": ["<name>"], "allow_fallbacks": false}`.
- Build the pin map from the live endpoints API (precision ranking: fp32 > bf16/fp16 > fp8 > int8 > fp4/int4).
- Record the served provider on every response and verify the pin held.
- Unpinned results are not comparable between models or across time. If you inherit unpinned data, re-run - don't rescue it.

## 4. Deterministic checks beat the LLM judge's own flags

The judges' "fabricated" flags disagreed sharply with a deterministic check (is this %-or-multiplier actually present in the input?) - one model went from 7/9 judge-flagged to 0/10 deterministic, another direction entirely for a different model. Manual inspection sided with the deterministic check every time: the judges blur invented factual claims with acceptable illustrative numbers, and drift run to run.

- Wherever a property can be checked by a program - numbers-in-input, banned phrases, length, JSON validity, required fields - the program is the metric.
- Keep the judge's flag as advisory color only; never let it carry a claim in the report.
- Corollary: before trusting ANY judge-derived number, spot-check a sample by eye against the raw outputs.

## 5. Hard and trap cases are mandatory

An easy-only golden set produced "scoring is 100% solved across all models." It survived - but only after hard cases were added could that claim honestly be made, and the hard cases are what would have caught it if false.

Include, per structured task:
- **Boundary cases** - inputs sitting exactly on a decision edge (score ~= the tier cutoff). Expect dispersion; grade calibration, not a single right answer.
- **False-trigger traps** - inputs that superficially match a hard rule but don't actually (personal email BUT company identified; enterprise size BUT exec contact). These measure precision - a model that over-fires a disqualifier silently rejects your best leads.
- **Buried positives** - the rule genuinely applies but is hidden in distracting commercial-looking context.

And state what the traps do NOT test: "the traps test false positives; a subtly hidden true-DQ was not tested" - name the untested direction.

## 6. Blind the panel

- Anonymize candidates to letters; **re-shuffle per case** so position and label carry nothing.
- Judges from **different model families than every candidate** - same-family judges have correlated style priors.
- 3+ judges; aggregate across judges as part of n, and check no single judge drives the ranking.
- Fixed judging temperature (0) and a written rubric; the panel judges quality against the rubric, not "which do you like."

## 7. Small-sample honesty

The verdict from 8 cases x 5 seeds is a verdict about THESE prompts, THESE providers, THIS month.

- State N cases, N seeds, N judge-scores per model in the report, every time.
- Say "on these prompts" - not "model A is better than model B."
- Prices, rankings, and even served quantizations change monthly - date-stamp the report and treat it as perishable.
- Sanity-check alarming rates against their base: 0/10 fabrication on 10 samples is indistinguishable from a true 5% rate. Widen the sample before making a purity claim load-bearing.

## The meta-rule

Before publishing any eval verdict, run the red-team pass the reference experiment ran: "what honest checks would embarrass this result?" Seeds, pins, hard cases, deterministic cross-checks - if any of them would plausibly flip the headline, do them first. The Round 2 -> Round 3 reversal happened because someone asked that question BEFORE publishing. Build the skill's reports so nobody has to ask.
