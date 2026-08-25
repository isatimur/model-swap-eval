# Migration decision report - <pipeline / task name>

**Date:** <YYYY-MM-DD> · **Incumbent:** <frontier model> · **Task volume:** <N calls/month, $X/month>
**Harness:** <N models> x <N cases> x <N seeds> = <N> cells, provider-pinned · eval cost $<X>
**Golden refs validated by:** <name - human sign-off, or "frontier-proposed, UNVALIDATED -> verdict is provisional">

## Verdict (one paragraph, up front)

<Swap / swap-with-guard / optimize-first / keep frontier - per task. One sentence each on why,
with the projected annual savings at stated volume. If the top is a statistical tie, SAY SO here:
"no single winner; pick on cost/speed/failure-rate among the tied cluster.">

## The tradeoff table

| Candidate | Quality (mean +/- 95% CI) | Accuracy loss vs frontier | $/call (mean +/- stderr) | $ saved | Latency p90 | Verdict |
|---|---|---|---|---|---|---|
| <frontier> (baseline) | x.xx [a.aa-b.bb] | - | $0.0000 +/- 0.0000 | - | xx s | baseline |
| <candidate 1> | x.xx [a.aa-b.bb] | xx% | $0.0000 +/- 0.0000 | xx% | xx s | tied / clearly worse |
| ... | | | | | | |

Quality source: <blind 3-judge panel (families: X, Y, Z) / deterministic pass-rate, CASE-clustered n=<cases>>.
Cost is the mean of API-reported cost across all cells - includes reasoning-token burn, not the price sheet;
carry its stderr because a single runaway cell can dominate the mean. State the minimum difference detectable
at this n (grade.py prints it) so "tied" is not misread as "equivalent".

## Statistical reading - tied vs worse

- **Top cluster (statistically tied, |z| <= 1.96):** <models>. Overlapping CIs - crowning a
  winner among these would be reading noise. Choose among them on cost, latency, and the
  deterministic failure rates below.
- **Clearly worse:** <models> (z = <values> vs top).
- Within-model spread across seeds: +/- <x.xx>; between-model range: <x.xx>. <If comparable:
  "per-model noise is on the order of the whole ranking - only the losers separate.">

## Deterministic findings (these carry more weight than the panel)

- **Fabrication** (metric-like numbers absent from the input), per model across <N> samples: <...>
- **Trap / boundary cases:** <N/M models fell for the false-trigger traps; boundary-case
  dispersion summary. Name what the traps do NOT test.>
- **Format / guard violations, empty outputs:** <...>

## Recommendation per task

| Task | Recommendation | Model (+ provider pin, quant) | Guard required | Projected annual savings |
|---|---|---|---|---|
| <task> | swap / swap-with-guard / optimize-first / keep frontier | <model @ provider, fp8> | <e.g. deterministic fabrication check + frontier verify-gate on ship> | $<X> |

<For optimize-first: what the autoresearch loop should target and the val-gate threshold.
For keep frontier: which cheaper levers (caching, prompt trim, batch tier) still apply.>

## Deployment notes

<Orchestration: smart model dispatches this task to the pinned cheap model - never the whole
agent. Guards wired into production. Rollback = one config flag. Re-run triggers: prompt change,
pin/quant change, notable new model, or quarterly.>

## Limitations (mandatory - do not trim)

This verdict is based on <N> cases x <N> seeds per model (<N> panel scores/model), on THESE
prompts, THESE pinned providers, as of <date>. It is not a general model ranking. Known blind
spots: <e.g. traps test false positives only; subjective panel n gives ~+/-x.x CI; 0/N failure
rates are indistinguishable from a true y% rate at this sample size; val set was consulted
once/never/more-than-once>. Prices and the model field turn over monthly - treat as perishable.
