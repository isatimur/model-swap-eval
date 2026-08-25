# Triage - when NOT to do this

The eval takes real effort (golden set + human validation + sweep + grading). Spend it only where a swap could actually pay. Walk every candidate task through these gates BEFORE Step 1. Refusing is a feature - do not run a sweep to be agreeable.

## Gate 1 - Is the spend material and recurring?

- The task must run at volume (daily/weekly, hundreds+ of calls) and be a material share of the LLM bill. If the user can't say what the task costs per month, run `token-audit` first - that number decides whether anything else here matters.
- **Refuse:** one-off or rare tasks. The eval costs more than years of the task's spend. Keep the frontier model and move on.
- **Refuse:** "migrate everything." The methodology migrates one narrow task at a time. Pick the fattest and start there.

## Gate 2 - Is the output checkable?

- There must be a way to say "this output is right/acceptable" - a reference answer, a deterministic rubric, or a subjective output the user will fund a golden set + blind panel for.
- **Refuse:** no checkable output AND no willingness to build a golden set. Without a metric, the "eval" would be vibes with a spreadsheet. Offer to help define a metric first; that's a different task.

## Gate 3 - Can quality dip at all?

- Every swap carries some accuracy-loss risk; the whole method exists to measure and bound it, not eliminate it.
- **Refuse:** safety-critical or reputation-critical outputs where ANY quality dip is unacceptable (medical/legal advice going to clients, compliance filings, irreversible money movements). The verdict is pre-determined - keep the frontier model - so skip the eval and say that. (A verify-gate architecture from `deployment.md` may still cut costs; offer it as the alternative.)

## Gate 4 - Is the frontier baseline itself reliable?

- The frontier model's output is about to become the reference oracle. If the pipeline is still flaky ON the frontier model - prompt still churning weekly, outputs still disappointing the user - there is no stable target to measure candidates against.
- **Refuse (for now):** fix the task on the frontier model first. "Make it work, then make it cheap" is the ordering. Revisit once the frontier pipeline is boring.

## Gate 5 - Is the task stable enough to amortize the eval?

- The verdict is perishable (models/prices turn over monthly) but the TASK should not be: if the prompt or the output contract is about to be redesigned, the golden set dies with it.
- **Defer:** tasks mid-redesign. Evaluate after the redesign lands.

## Gate 6 - Is this actually a model-cost problem?

Before benchmarking replacements, check the cheaper levers on the CURRENT model - often the audit reveals the spend is waste, not model price:
- prompt bloat (repeated context, unpruned history) - trimming can beat a model swap without any accuracy question
- caching (repeated identical prefixes/calls), batching, or a batch-API discount tier
- the task running more often than needed
If one of these erases most of the spend, do it first; a smaller residual may fail Gate 1 and save the whole eval.

## The refusal template

> This task isn't a good migration candidate because [gate N fails: specific reason]. Running the eval anyway would [cost more than it can save / measure nothing / answer a question whose answer is already "keep the frontier model"]. Instead:
>
> 1. [The alternative that addresses the actual blocker - token-audit, metric definition, prompt stabilization, caching/trimming, verify-gate architecture]
> 2. [If applicable: a narrower sub-task that DOES pass the gates]
>
> Want me to start there?

## Quick-scan examples

| Candidate | Verdict | Why |
|---|---|---|
| Lead scoring, 1000/mo, JSON rubric output | **Proceed** | recurring, fat, deterministic-checkable |
| Outbound copy drafts, 500/mo | **Proceed** (panel path) | recurring, subjective but golden-set-fundable |
| Quarterly board-deck narrative | Refuse (Gate 1) | 4 runs/year - keep frontier |
| "Is our agent's strategy good?" | Refuse (Gate 2) | no checkable output; that's a council question |
| Client-facing legal filings | Refuse (Gate 3) | zero quality-dip tolerance; offer verify-gate instead |
| Pipeline whose prompt changed 3x this month | Refuse (Gate 4/5) | no stable oracle yet |
| "Claude bill too high" but 60% is prompt bloat | Redirect (Gate 6) | trim first, re-audit, then maybe eval |
