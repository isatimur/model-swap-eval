# Metrics - defining "accuracy loss" per task type

The metric is chosen in Step 1, before any data exists. If you cannot define what accuracy loss means for the task, you cannot run this eval - go back to triage.

## The decision table

| Output kind | Examples | Grading method | "Accuracy loss" means |
|---|---|---|---|
| **Categorical** | tier/label/class, yes-no, routing decision | exact-match vs reference | drop in pass-rate vs frontier (pp) |
| **Numeric** | score, price, count, extracted quantity | absolute error vs reference, within-tolerance rate | MAE increase vs frontier; or within-tolerance rate drop |
| **Structured JSON** | multi-field extraction, rubric scoring | field-level exact-match + numeric tolerance + JSON-validity | field pass-rate drop; validity failures count as full misses |
| **Constrained text** | outputs with hard rules (length, banned phrases, must-mention, no invented facts) | deterministic guard checks + (optionally) panel for residual quality | guard-violation rate delta + panel delta |
| **Subjective text** | outbound copy, posts, summaries, voice writing | blind multi-family LLM panel, 0-10 | (frontier mean - candidate mean) / frontier mean, with CI |

Mixed outputs are graded in layers: deterministic guards first (they are ground truth), panel only for what remains genuinely subjective.

## Deterministic beats judge - the ordering rule

For any property that CAN be checked programmatically, the program is the metric and the judge's opinion on that property is discarded. The reference experiment's LLM judges disagreed sharply with a simple "is this number actually present in the input?" check on fabrication - and manual inspection sided with the check. Judges blur "fabricated fact" with "illustrative number" and drift between runs.

Deterministic checks worth building for text tasks:
- **Fabrication:** extract metric-like tokens (percentages, multipliers, large integers) from the output; any not present in the input is an invented claim. Report per-model fabrication rate across seeds.
- **Format guards:** word/character bounds, required structure, banned-phrase list (AI-speak, hype words), JSON validity.
- **Grounding:** required entities/facts from the input that must appear.

## The blind panel (subjective tasks only)

Rules - all mandatory, rationale in `rigor.md` #6:
- **Anonymize and shuffle** candidates per case (fresh shuffle each case) - labels A, B, C carry no information.
- **3 judges from families different from every candidate** (e.g. judging open-weight Chinese-lab models with xAI + OpenAI + Google judges). Same-family judges favor their siblings' style.
- Judge at **temperature 0** with a task-specific rubric that names what "good" is; include an explicit `fabricated` flag but treat it as advisory (the deterministic check is the real fabrication metric).
- Score every seed's output, not one - the panel runs over seeds x cases x judges, giving n ~= 45 scores per model from 3 cases x 5 seeds x 3 judges.
- Aggregate mean +/- std +/- stderr, 95% CI. `templates/judge.py` implements all of this.

## Aggregation and comparison

- Per model: mean quality, std, stderr = std/sqrt(n), 95% CI = mean +/- 1.96 x stderr.
- Frontier baseline defines 100%: accuracy loss = (frontier mean - candidate mean) / frontier mean.
- Pairwise separation: z = (mean1 - mean2) / sqrt(se1^2 + se2^2). |z| > 1.96 = clearly different; otherwise **statistically tied** - report it as a tie (and per rigor.md #2, a tie is absence of evidence, not equivalence; report the minimum difference detectable at your n).
- For pass-rates, aggregate at the CASE level, not the cell level: each model's per-case pass fraction, then stderr = stdev(per-case fractions) / sqrt(n_cases). Do NOT treat N seeds x M cases as N*M iid Bernoulli trials - at temperature 0 the seeds are near-replicates, so effective n = M cases (rigor.md #2). `grade.py` does this; the same z-test applies to the case-clustered means.
- Boundary cases in the golden set have no single right answer - grade them on calibration (does the model land in the expected dispersion band?) and report the spread, not a pass/fail.
- Trap cases get their own line in the report: "N/9 models fell for the false-trigger trap" is often the single most decision-relevant number.

## Cost and latency metrics

Measured from the sweep itself, not price sheets:
- $/call = mean of the API-reported cost across all cells (price sheets miss reasoning-token burn - a "cheap" runaway reasoner can out-spend a mid-price model).
- Savings % = 1 - candidate $/call / frontier $/call; project to annual $ at the user's stated volume.
- Latency: mean and p90 seconds per call; flag reasoning-token counts (high reasoning = latency and cost variance).
- Empty/failed outputs are an operational metric too - a model that returns nothing 10% of the time needs a retry budget in production.
