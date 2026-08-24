# The decision procedure - full 8-step methodology

Bayram's taught workflow for deciding whether an expensive LLM pipeline can move to a cheaper or open-source model, hardened by the rigor lessons in `rigor.md`. The steps are ordered; each produces an artifact the next one consumes.

## Step 0 - Identify the fattest tasks

Do not try to migrate "the pipeline." Migrate the one or two tasks that dominate spend.

- Ask: which recurring, high-token-volume tasks cost the most money per month? Canonical example: lead scoring - 1000 leads reviewed to land 100 clients, every lead scored by a frontier model - a huge, perfectly repeatable spend.
- If the user doesn't know their spend distribution, run the `token-audit` skill first. Its output (task x frequency x tokens x $/month) is exactly the input this step needs.
- A migration candidate must be: **high-frequency** (runs daily/weekly at volume), **high-cost** (material % of the LLM bill), **repeatable** (same prompt shape every time), and have a **checkable output** (a reference answer or a gradeable rubric can exist).
- Run the triage checklist (`triage.md`) on each candidate. Refusing here saves the whole eval.

**Artifact:** a ranked list of 1-3 candidate tasks with monthly $ volume each.

## Step 1 - Frame the task and choose the accuracy metric

Define what "accuracy loss" MEANS for this task before measuring anything. See `metrics.md` for the decision table. The short version:

- **Structured output** (JSON, label, tier, number, extraction): deterministic grading - exact-match on categorical fields, tolerance/MAE on numeric fields, format-validity rate. No judge needed.
- **Subjective output** (copy, prose, summaries where "good" is a taste question): blind LLM panel - but strip out every sub-property that CAN be checked deterministically (length bounds, banned phrases, fabricated numbers, required mentions) and check those programmatically.

Also fix, per task: temperature (use the production value), max_tokens (generous - reasoning models silently emit empty content when starved), and the exact production system prompt (evaluate the task as it actually runs, not an idealized version).

**Artifact:** per task - output kind, grading method, and a one-sentence definition of "X% accuracy loss."

## Step 2 - Build the golden / reference dataset

- ~10-20 cases per task. Use real production inputs where possible.
- Generate proposed reference outputs with the frontier model (the incumbent - Opus/Fable class). `templates/build_golden.py` scaffolds this.
- **Human validation is not optional.** The proper form: human says 5, Opus says 10, Qwen says 8 - you cannot grade Qwen until the human and Opus agree. First reconcile the frontier model with the human on every case (fix the prompt or fix the reference), THEN the frontier output becomes the oracle. An unvalidated oracle just measures agreement-with-Opus, including Opus's mistakes.
- **Include hard cases** - mandatory, see `rigor.md` #5:
  - boundary cases (inputs that sit on a decision edge - e.g. a lead scoring exactly at the Cold/Warm cutoff; record the expected dispersion, not a single "right" answer)
  - false-trigger traps (inputs that LOOK like they hit a hard rule but don't - e.g. a gmail address but the company IS identified; an enterprise but the contact IS an exec). These measure rule precision, not just recall.
  - genuinely hard positives (the rule really does apply, but it's buried in distracting context)
- **Split dev / test / val** now (roughly 50/30/20), even if Step 7 might never run. If prompt optimization happens later without a held-out split, every reported number is overfit. Splits are cheap to assign and impossible to un-contaminate.

**Artifact:** `tasks.py` with cases + human-validated references + splits.

## Step 3 - Pick candidate models

See `candidate-selection.md` for the full method. Summary:

- Cross a **quality** leaderboard (Artificial Analysis Intelligence Index) with a **usage** leaderboard (OpenRouter rankings) - fetched live, both. Quality-only picks miss the models the market actually runs; usage-only picks miss quality cliffs.
- Shortlist 4-8 candidates spanning the price range - include at least one ultra-cheap model (they sometimes win on structured tasks) and the current price/quality sweet spot.
- Evaluate through **OpenRouter first**: one API, one bill, 400+ models. The local-vs-cloud decision (Ollama etc.) comes AFTER the accuracy verdict, driven by security/deployment requirements - see `deployment.md`.
- Build the **provider-pin map** now (`templates/pick_candidates.py`): for each candidate, fetch its endpoints and pin the highest-precision quantization (bf16/fp16 > fp8 > int8 > fp4/int4) with `allow_fallbacks: false`. See `rigor.md` #3 for why this is mandatory.

**Artifact:** `MODELS` list in `tasks.py` + `provider_pins.json`.

## Step 4 - Sweep: every candidate, N seeds per case

- Default **5 seeds** per model x case (floor: 3). Non-deterministic systems need repeats; the spread IS data.
- **Include the incumbent frontier model in the sweep.** Its seed variance is the noise floor for the whole eval, and its measured $/call (not the price sheet) anchors the savings math.
- Record per call: content, cost, latency, completion/reasoning tokens, finish_reason, and the provider that actually served the request (verify the pin held).
- Run concurrent (5-10 workers), retry on 402/429/5xx with backoff, save incrementally, resume from partial output. `templates/run_sweep.py` does all of this.
- Watch for empty completions - runaway reasoners burn the token budget on thinking and return nothing. An empty is a data point (count it as a failure), not a retry-until-it-works.

**Artifact:** `outputs/seeds_raw.json` - one record per task x case x model x seed.

## Step 5 - Score by closeness to reference, with a proper error metric

- Run `templates/grade.py` (deterministic metrics) and, for subjective tasks, `templates/judge.py` (blind panel).
- Compute per model: quality mean +/- stderr and 95% CI; for numeric tasks an MAE-style average error vs reference; pass-rates for structured tasks.
- Express the headline the way a decision-maker reads it: "Kimi scores 7.6/10 vs the frontier's 9.5 (~25% quality loss) at 80% lower cost."
- Then the honesty pass (`rigor.md` #2): pairwise z-tests against the top scorer; label each candidate **statistically tied** or **clearly worse**. Expect a tied top cluster.
- Assemble the tradeoff table: candidate | quality mean +/- CI | accuracy loss vs frontier | $/call | $ saved % | latency | verdict.

**Artifact:** the tradeoff table + per-task deterministic findings (fabrication rates, trap-case results).

## Step 6 - Decide

- The acceptable cost-vs-accuracy ratio is a **business decision the user makes**, not a number the eval produces. 25% quality loss for 80% savings is a great trade for internal lead scoring and a terrible one for customer-facing copy.
- Present per task one of four recommendations:
  - **swap** - a candidate is statistically tied with the frontier (or the loss is inside the user's stated tolerance)
  - **swap-with-guard** - tied on quality but with a known failure mode (e.g. occasional fabrication) that a deterministic guard or verify-gate covers
  - **optimize-first** - close but under threshold; Step 7 has a realistic shot
  - **keep frontier** - no acceptable swap exists. Say so plainly; this is a successful eval outcome.
- Write the report from `templates/report_template.md`. If the verdict is contested or strategic (e.g. brand risk vs savings), a `council` pass on the finished table is cheap.

**Artifact:** the decision report.

## Step 7 (optional) - Close the gap via optimization

Only when the best candidate is close-but-not-enough. Full discipline in `optimization.md`. The short version: run an autoresearch-style loop that mutates the cheap model's prompt with the dev-set score as fitness, gate on test, confirm ONCE on val. The model will overfit to any set it sees repeatedly - the val number is the only one that goes in the report.

## Step 8 - Deploy via orchestration

Full patterns in `deployment.md`. The core rule: **never run the whole agent on the cheap model** - swap the one narrow task you evaluated, keep the smart model as orchestrator, encode the routing as "for THIS task type, use THIS model," and keep a verify-with-a-smarter-model gate on outputs that matter.

## Cost of the eval itself

For calibration: the reference experiment - 9 models x 8 cases x 5 seeds = 360 generation cells plus a 3-judge panel - cost about $1.15 total on OpenRouter. The eval is cheap; running the wrong model for a year is not. Do not skimp on seeds to save cents.
