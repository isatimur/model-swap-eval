# Optimization - closing the gap (Step 7)

Run this only when Step 6 lands on "optimize-first": the best candidate is close to the threshold but under it, and the gap looks prompt-shaped (formatting misses, missed rubric nuances, tone drift) rather than capability-shaped (can't follow the rubric at all, hallucinates structure).

## The loop

This is the `autoresearch` skill applied with a specific fitness function - use that skill for the loop mechanics (keep-or-revert, one atomic change per iteration, plateau detection). What this reference adds is the eval-specific discipline.

- **Goal:** cheap model's quality on this task >= the user's threshold (e.g. >= 9/10 panel mean, or >= 95% pass-rate).
- **Mutable surface:** the task prompt (system prompt, rubric wording, few-shot examples) for the CHEAP model only. The golden references, the grading code, and the judge rubric are frozen - if the loop can touch the ruler, it will optimize the ruler.
- **Fitness:** the same grading harness from Step 5, run on the **dev split only**, all seeds. One command, one number.
- **Budget:** iterations are cheap (a dev-split eval on one model costs cents) - 20-50 iterations is normal.

## Train/test/val discipline - the critical part

The model overfits to whatever dataset it sees repeatedly. A prompt tuned against 10 visible cases learns those 10 cases - the dev-set score climbs while generalization doesn't. This is not hypothetical; it is the default outcome.

- **dev (~50%):** the loop's fitness set. Seen every iteration. Its score is a tuning signal, never a reported result.
- **test (~30%):** the gate. Evaluate the best prompt on test occasionally (every ~10 iterations or at candidate-promotion time). If dev climbs and test doesn't, the loop is overfitting - stop, simplify the prompt, or accept the gap as capability-shaped.
- **val (~20%):** touched ONCE, at the very end, by the final prompt. The val number is the only number that goes in the decision report. If val disappoints, you do NOT get to iterate against it - that would just convert val into a second dev set. Either ship the test-gated result with honest caveats or grow a fresh val set.

Assign splits in Step 2, before anyone has seen any model's outputs on them (`templates/build_golden.py` does this deterministically and persists them to `outputs/splits.json`). Adding cases later PRESERVES every existing case's split - only the genuinely new cases are assigned - so growing the golden set never silently moves a case from val into dev mid-project. If you must re-balance splits, do it as a deliberate, logged reset before any tuning, never after.

## What the loop may and may not change

May: instructions, rubric phrasing, few-shot examples, output-format scaffolding, explicit guards ("never state a number not present in the input").
May not: the reference answers, the metric, the seeds, the temperature, the judge rubric, the case set. Any of these "improving" during the loop invalidates the comparison to the frontier baseline measured in Step 5.

If the frontier baseline used a different (production) prompt, that is fine - the comparison is "frontier as it runs today vs cheap model with its tuned prompt," which is the actual business question. Note the asymmetry in the report. Optionally also give the frontier the tuned prompt for a symmetric read.

## When to stop

- **Success:** val-confirmed score clears the threshold -> recommendation upgrades to swap (or swap-with-guard).
- **Plateau:** dev stops improving, or dev-test divergence appears. Diagnose with the autoresearch plateau workflow; usually the residual gap is capability-shaped.
- **Capability-shaped gap:** the remaining errors are the same on every seed and unmoved by prompt changes. Options, in order of increasing effort: swap-with-guard (deterministic guard or verify-gate covers the failure mode - see `deployment.md`), fine-tuning (see `deployment.md`), or keep frontier.

## Honest reporting after optimization

- Report the val score, with CI, labeled "after prompt optimization, on held-out cases."
- Report the pre-optimization score alongside - the delta is the finding.
- State that the tuned prompt is task-specific: it transfers to production, not to other tasks or other models.
- If val was consulted more than once, say so - the number is then optimistic and the report must carry that caveat.
