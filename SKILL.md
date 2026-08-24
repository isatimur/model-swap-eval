---
name: oss-migration-eval
description: Decide rigorously whether to switch an expensive LLM pipeline (Opus/Fable-class) to a cheaper or open-source model - estimate cost savings vs accuracy loss and produce an honest go/no-go verdict with error bars. Use when the user asks "can a cheaper/OSS model replace X", "switch/migrate this pipeline to open source", "downshift the model", "which model can replace X", "am I overpaying for this LLM task", "reduce token cost", "evaluate OSS alternatives", or "cost vs accuracy". Builds a frontier-model golden set (incl. trap cases), sweeps provider-pinned OpenRouter candidates across seeds, and reports statistically-tied vs clearly-worse - it refuses bad-fit candidates (one-off tasks, no checkable output, safety-critical quality bars, pipelines the frontier model itself hasn't stabilized).
---

# OSS Migration Eval

The methodology in one breath: find the fattest recurring task, build a human-validated golden set on the frontier model (with trap cases), sweep cheap candidates 5 seeds each on pinned providers, grade deterministically where possible, and present cost-savings vs accuracy-loss as a business decision - then deploy the swap behind a smart orchestrator, never as a full-agent replacement.

**The hard-won lesson: most eval "winners" are single-sample noise.** A real experiment crowned a decisive 9.00/10 winner off one generation per cell; a 5-seed rigor pass dropped it to 7.00 +/- 2.0, mid-pack in a six-way statistical tie. Within-model variance across seeds nearly equaled the entire between-model ranking spread. This skill's job is an honest cost-vs-accuracy verdict - and often the honest verdict is "no clear winner, here is the tradeoff; you can only name the losers." Saying that is a success, not a failure.

## When this skill applies

Invoke when the user:
- wants to replace a frontier model in a pipeline with something cheaper or open-source
- asks "which model can replace X for this task", "can I downshift this", "am I overpaying"
- wants a cost-savings vs accuracy-loss estimate for a recurring LLM task
- asks to evaluate open-weight models (Kimi, DeepSeek, GLM, Qwen, Llama, ...) against their current setup
- wants to move a task to a local model (Ollama) for cost or data-security reasons

Do NOT invoke for (see `references/triage.md` for the full gates and refusal template):
- one-off or low-volume tasks - the eval costs more than years of the task's spend; keep the frontier model
- tasks with no checkable output and no willingness to build a golden set - nothing to measure means nothing to decide
- safety-critical outputs where any quality dip is unacceptable - the answer is already "keep the frontier model" (offer a verify-gate architecture instead)
- pipelines where the frontier model itself is not yet reliable - fix the task first, then ask about cost
- general model gossip ("which model is best?") - this skill answers "best for THIS task at THIS price," nothing broader

Refusal is a feature. Do not run a sweep to be agreeable.

## Non-negotiable rigor rules

Baked into every step; full rationale and the war stories in `references/rigor.md`:

1. **>= 3 seeds per model x case, default 5.** Single-seed rankings are noise. Always report mean +/- stderr / 95% CI.
2. **Report ties honestly.** Compute pairwise z = delta-mean / sqrt(se1^2 + se2^2); |z| > 1.96 separates. Overlapping CIs = "statistically tied," never a crowned winner. Expect to identify losers, not a winner.
3. **Pin every candidate to one highest-precision provider** (`allow_fallbacks: false`). Unpinned OpenRouter routing serves different quantizations (fp4/int4 vs fp8/bf16) call to call - a benchmark lottery.
4. **Deterministic grading beats an LLM judge wherever the task allows.** A programmatic "is this number actually in the input?" check contradicted the judges' fabrication flags - and was right. The blind panel is only for genuinely subjective output.
5. **Hard and trap cases are mandatory in the golden set.** Easy-only sets over-claim "solved." Include boundary cases and false-trigger traps.
6. **Blind the panel:** anonymize + shuffle candidates per case, use judge models from different families than the candidates.
7. **Small-sample honesty:** every verdict states N cases, N seeds, and "on these prompts" - never "model A is better," period.

## The eight steps

Full procedure with rationale and per-step artifacts in `references/methodology.md`. Work through them in order; skip only what the user has explicitly already done. Each step names WHAT to establish - the executing agent chooses tools and specifics.

### Step 0 - Find the fattest tasks

- Establish which recurring, high-token-volume tasks dominate the LLM bill. If the user doesn't know, run the `token-audit` skill - its task x frequency x $/month output is exactly this step's input.
- A migration candidate must be high-frequency, high-cost, repeatable, and have a checkable output. Migrate one narrow task at a time, never "the pipeline."
- Run the triage gates (`references/triage.md`) on each candidate now: material spend, checkable output, tolerance for any quality dip, a stable frontier baseline, a stable task definition, and "is this even a model-cost problem or is it prompt bloat/caching."
- Refuse or redirect bad fits here, before any harness exists.
- Once `tasks.py` has a first draft, run `templates/preflight.py validate` (free, offline) before spending anything - it catches schema mistakes (duplicate case ids, an unsigned-off pasted ref, a bad `kind`) that the later scripts would otherwise discover mid-sweep. `preflight.py estimate` (needs `OR_KEY`) gives a live worst-case $ ceiling before `run_sweep.py` runs.

### Step 1 - Frame the task, choose the accuracy metric

- Classify each task's output: structured (JSON/label/number -> deterministic exact-match or numeric error) or subjective (copy/text -> blind panel). `references/metrics.md` has the decision table.
- Define what "X% accuracy loss" MEANS for this task in one sentence, before any data exists. If that sentence can't be written, return to triage.
- For text tasks, strip out every sub-property that CAN be checked deterministically (length, banned phrases, fabricated numbers, required mentions) - those get programmatic guards, not judge opinions.
- Fix the eval conditions to production reality: the exact production system prompt, the production temperature, generous max_tokens (reasoning models silently return empty when starved). Note `max_tokens` enforcement is *provider-dependent* (some providers honor it, some ignore it) and never caps *reasoning* burn - a runaway reasoner can spend tens of thousands of tokens regardless, inflating measured $/call and latency; `run_sweep.py` flags any cell exceeding that model's median or an optional `MAX_COST_PER_CELL`, and those inflated costs are real - they belong in the report.

### Step 2 - Build the golden set

- ~10-20 cases per task from real production inputs; generate proposed references on the frontier model (`templates/build_golden.py`).
- **Human validation is not optional, and the scripts enforce it on BOTH reference paths.** Human says 5, Opus says 10, Qwen says 8 - first make the frontier model agree with the human, THEN it becomes the oracle. An unvalidated oracle measures agreement-with-Opus, including Opus's mistakes. `grade.py` refuses any reference not signed off: a `ref` pasted into `tasks.py` needs `"validated": true`; a `golden_proposed.json` fallback needs `"human_validated": true`. No sign-off, no verdict.
- Include hard cases - mandatory: boundary cases (inputs on a decision edge - grade calibration, not a single right answer), false-trigger traps (inputs that LOOK like they hit a hard rule but don't - these measure precision), and buried positives.
- Split dev / test / val (~50/30/20) now, even if optimization may never happen - splits are cheap to assign and impossible to un-contaminate later.

### Step 3 - Pick candidate models

- Shortlist 4-8 candidates by crossing a quality leaderboard (Artificial Analysis Intelligence Index) with a usage leaderboard (OpenRouter rankings) - fetch both LIVE; the field turns over monthly and benchmark rank != task fit. Method and task-type priors: `references/candidate-selection.md`.
- Span the price range: at least one ultra-cheap model (they sometimes tie on structured tasks - the biggest savings if it holds) plus the current price/quality sweet spot. Include the incumbent frontier model as baseline.
- Evaluate through OpenRouter first (one API, one bill, 400+ models); the local-vs-cloud decision comes after the accuracy verdict, from security/deployment requirements.
- Build the provider-pin map now (`templates/pick_candidates.py`): for each candidate, pick the highest-precision endpoint (bf16/fp16 > fp8 > int8 > fp4/int4) and pin it with `allow_fallbacks: false`.
- Load real credit (~$10) before sweeping - thin OpenRouter accounts 402 on pricier models and silently bias the sweep toward cheap ones.

### Step 4 - Sweep

- Run every candidate AND the incumbent through the same harness: N seeds (default 5) per case, pinned providers, recording content + cost + latency + reasoning tokens + the provider that actually served each call (`templates/run_sweep.py` - concurrent, resumable, incremental save).
- The frontier goes through too: its seed variance is the eval's noise floor, and its measured $/call (not the price sheet) anchors the savings math.
- Empty completions from runaway reasoners are data points (failures), not retry-until-it-works. Verify the pins held on every response.

### Step 5 - Grade and build the tradeoff table

- Deterministic metrics per task type via `templates/grade.py`: pass-rates with binomial stderr, MAE, fabrication check (numbers absent from input), format guards, trap-case results, cost/latency aggregation with savings vs the measured frontier baseline.
- Blind multi-family panel for subjective tasks via `templates/judge.py`: anonymized + re-shuffled per case, 3 judges from non-candidate families, temperature 0, scored across seeds x cases x judges, aggregated mean +/- std +/- stderr with 95% CI.
- Run the honesty pass: pairwise z vs the top scorer; label every candidate **statistically tied** or **clearly worse**. Expect a tied top cluster.
- Express the headline the way a decision-maker reads it: "candidate scores 7.6 vs the frontier's 9.5 (~25% quality loss) at 80% lower cost."
- Assemble the core artifact: candidate | quality mean +/- CI | accuracy loss vs frontier | $/call | $ saved % | latency | tied-or-worse.

### Step 6 - Decide

- The acceptable savings-per-quality-point ratio is a BUSINESS decision the user makes - 25% loss for 80% savings is a great trade for internal scoring and a terrible one for customer-facing copy. Present the table; don't decide for them.
- Recommend per task one of four: **swap** (tied, or loss inside stated tolerance) / **swap-with-guard** (tied but with a known failure mode a deterministic guard or verify-gate covers) / **optimize-first** (close but under threshold) / **keep frontier** (no acceptable swap - say it plainly; a valid, successful outcome).
- Write the report from `templates/report_template.md`, limitations paragraph included - `templates/build_report.py` assembles the tradeoff table, tied-vs-worse reading, and limitations paragraph mechanically from `grade_agg.json`/`judge_agg.json`, but the Verdict and per-task Recommendation stay `<FILL IN>`: that call is the user's, not the tool's. For contested or strategic verdicts (brand risk vs savings), a `council` pass on the finished table is cheap.

### Step 7 (optional) - Close the gap

- Only when the best candidate is close-but-under and the gap looks prompt-shaped, run a prompt-optimization loop - the `autoresearch` skill with the cheap model's dev-split score as the fitness function. Discipline in `references/optimization.md`.
- CRITICAL: the model overfits to whatever set it sees. Tune on dev, gate on test, confirm ONCE on untouched val - the val number is the only one that enters the report. If val disappoints, you do not get to iterate against it.
- The references, grading code, seeds, and judge rubric are frozen during the loop - if the loop can touch the ruler, it optimizes the ruler.

### Step 8 - Deploy via orchestration

- Never move the whole agent to the cheap model - the eval validated one narrow task, not planning, tool routing, or recovery; whole-agent downshifts collapse exactly there. Patterns in `references/deployment.md`.
- The smart model orchestrates; the cheap model executes the evaluated task via a subagent, script, or direct call. Encode the routing declaratively - "for THIS task type, use THIS model (THIS pin, THIS prompt version)" - in a skill or dispatch table, dated and tied to the report.
- Keep a verify-with-a-smarter-model gate on outputs that matter (cheap model does the volume, frontier reviews the last step or a sample) and run the deterministic guards from the eval on 100% of production outputs.
- Local via Ollama when data must not leave the machine - but if production runs a heavier quant than the eval pinned, re-run the sweep against that quant first; it is a different model. Fine-tuning (HuggingFace ecosystem) is the last resort for stubborn capability-shaped gaps on custom tasks - and the fine-tune re-enters the harness as just another candidate row.
- Date-stamp the decision; re-run on prompt change, pin/quant change, notable new model, or quarterly. The harness costs ~$1 to re-run; keep the frontier path one config flag away.

## Reference files map

- `references/triage.md` - Step 0 (the six gates, when a swap is a bad idea, the refusal template)
- `references/methodology.md` - the full 8-step procedure with rationale and artifacts
- `references/candidate-selection.md` - Step 3 (leaderboards, shortlist composition, task-type priors, provider pinning, account gotchas)
- `references/metrics.md` - Steps 1 + 5 (accuracy-loss definition per task type, panel rules, aggregation math)
- `references/rigor.md` - the seven honesty guardrails and the experiment that motivated them
- `references/optimization.md` - Step 7 (autoresearch loop + train/test/val discipline)
- `references/deployment.md` - Step 8 (orchestration, verify-gate, Ollama, fine-tuning, operating the swap)

Load each on demand during the step that needs it. Do not preload.

## Templates map and run order

All templates read the API key from env `OR_KEY`, are resumable, and print progress. They share one contract: a `tasks.py` the agent fills in with the user - schema documented at the top of `templates/build_golden.py` (tasks, cases, references, splits, models, seeds, judges, banned phrases).

Typical run order in a fresh working directory:

1. Write `tasks.py` (tasks + cases; references empty where unknown)
2. `preflight.py validate` - offline schema check, free, before anything else touches an API
3. `preflight.py estimate` - live pricing -> worst-case $ ceiling + a remaining-credit check
4. `build_golden.py` - propose frontier references, assign splits, STOP for human validation
5. `pick_candidates.py discover` then `pin` - live shortlist data + `provider_pins.json`
6. `run_sweep.py` - the N-seed pinned sweep -> `outputs/seeds_raw.json`
7. `grade.py` (local, free) and, for subjective tasks, `judge.py` - metrics, error bars, tradeoff table
8. `build_report.py` - assembles `outputs/report.md` from the above; fill in the `<FILL IN>` business-call spots

- `templates/preflight.py` - `validate` (offline schema linter: duplicate ids, unsigned-off refs, bad kind, judge-family overlap) and `estimate` (live pricing -> worst-case cost ceiling + credit check) - catches mistakes and cost surprises before they cost money
- `templates/build_golden.py` - frontier reference proposals + dev/test/val splits + hard-case warnings
- `templates/pick_candidates.py` - live OpenRouter models/endpoints, highest-precision provider-pin map, shortlist table
- `templates/run_sweep.py` - N-seed, provider-pinned, concurrent, resumable sweep; records content + cost + latency + served provider
- Providers: `"openrouter"` (default, unchanged from upstream) or `"openai_responses"` (direct
  OpenAI Responses API, `strict` JSON-schema mode) - declared per model in `tasks.py`'s `MODELS`.
  See `references/providers.md`.
- `templates/grade.py` - deterministic metrics per task type, pass-rate/MAE with error bars, pairwise z-tests, cost-savings table; also persists `outputs/grade_agg.json` for `build_report.py`
- `templates/judge.py` - blind, shuffled, multi-family panel across seeds; mean +/- std +/- stderr with CI; advisory-only fabrication flags; persists `outputs/judge_agg.json`
- `templates/build_report.py` - mechanically assembles `outputs/report.md` from `grade_agg.json`/`judge_agg.json` - never decides the verdict or per-task recommendation itself; those stay `<FILL IN>` placeholders (rigor.md: that judgment is the user's)
- `templates/report_template.md` - the decision report skeleton `build_report.py` follows
- `templates/selftest.py` - offline regression suite for every template's non-network logic (no API key needed); run after editing any template; CI runs it on every push/PR

Adapt the templates to the task; they are scaffolds, not a framework. Keep the harness boring - the thinking lives in metric choice and the verdict. For calibration: the reference experiment's full harness (9 models x 8 cases x 5 seeds + 3-judge panel) cost ~$1.15; never cut seeds to save cents.

## What you get

A decision report (per `templates/report_template.md`) containing:
- the tradeoff table: every candidate's quality mean +/- CI, accuracy loss vs the frontier baseline, measured $/call, % saved, latency
- the honest reading: which models are statistically tied at the top, which are clearly worse (the losers separate; the winner usually doesn't)
- deterministic findings that outrank the panel: fabrication rates, trap-case results, guard violations, empty-output rates
- a per-task recommendation - swap / swap-with-guard / optimize-first / keep frontier - with projected annual savings at the user's volume and the required guards
- an explicit limitations paragraph: seeds, case count, "on these prompts, these providers, this month"

## Freshness - the skill must never rot

The model landscape turns over monthly; a hardcoded winner is wrong within weeks. This skill stays current by construction:

- **No model, price, rank, provider, or quantization is ever authoritative from memory.** Every one is fetched live at run time - `pick_candidates.py` for models/prices/endpoints; artificialanalysis.ai + openrouter.ai/rankings for the two leaderboards (fetched by the agent). Any model name appearing anywhere in this skill or its references is illustrative of a ROLE ("a frontier model", "an ultra-cheap candidate", "a vision-family model") - never a recommendation.
- **`tasks.py` holds the only model list, and the user fills it live** from that step - the templates never ship a model roster.
- **Every report is date-stamped and perishable** (`rigor.md` #7): prices, rankings, and even served quantizations drift month to month.
- **Re-run triggers** (`deployment.md`): the task prompt changes, a pin or quant changes, a notable new open-weight model ships in the task's family, or quarterly - whichever comes first. The harness is resumable and costs ~$1; staleness is never worth more than a dollar.

## Style rules for this skill

- Declarative: tell the executing agent WHAT to establish, not exact commands - it picks the tools
- Fetch leaderboards, prices, and model lists live; never assert them from memory
- Reuse existing skills by reference: `token-audit` (Step 0), `autoresearch` (Step 7), `council` (contested verdicts) - do not reimplement them
- Every number ships with its error bar and its sample size
- Use plain `-` hyphens, never em dashes
