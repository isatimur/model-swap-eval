# Deployment - shipping the swap (Step 8)

The eval said "swap." This reference is how to swap without the quality collapse that gives cheap models a bad name.

## The orchestration rule

**Never run the whole agent on the cheap model.** The eval validated ONE narrow task under ONE prompt - not planning, not tool selection, not recovery from weird states. Whole-agent downshifts collapse in exactly those unevaluated places.

The pattern:
- The **smart model orchestrates**: it owns the conversation, the plan, the tool routing, the edge cases.
- The **cheap model executes** the narrow, evaluated task - dispatched as a subagent, a script, or a direct API call with the exact prompt and temperature from the eval.
- Encode the routing declaratively: "for THIS task type, use THIS model (THIS provider pin, THIS prompt version)." A skill file or a dispatch table in the pipeline - somewhere reviewable, dated, and tied to the eval report that justified it.
- Batch-shaped tasks (score 1000 leads) often don't need an agent at all at execution time: a plain script calling the cheap model, with the orchestrator invoking the script.

## The verify-with-a-smarter-model gate

For outputs that matter (customer-facing, money-moving, irreversible), keep a final review step on a smarter model: the cheap model does the volume work, the frontier model reviews the last step or a sample.

- Full gate: frontier reviews every output before it ships (still cheap - review tokens << generation tokens, and the cheap model did the expensive volume).
- Sampled gate: frontier reviews N% plus every output that trips a deterministic guard.
- Deterministic guards from the eval (fabrication check, format guards, banned phrases) run on 100% of outputs regardless - they are nearly free and they caught real failures in the eval; wire the same code into production.
- This is also the "swap-with-guard" recommendation made concrete: the guard is what converts a tied-but-flawed candidate into a shippable one.

## Local deployment via Ollama

Decide local-vs-cloud AFTER the accuracy verdict, on requirements:
- **Data security:** input data never leaves the machine - the decisive argument for regulated or confidential pipelines. Often this, not cost, is the real reason to go local.
- **Cost shape:** local converts per-token spend into fixed hardware; wins at sustained high volume, loses at spiky low volume.
- Check which quantization your hardware forces. The eval pinned a high-precision cloud endpoint; a heavily-quantized local copy is a DIFFERENT model. If production will run q4 locally, re-run the sweep against a comparable quant (or the local endpoint itself - the harness only needs a base-URL change) before trusting the eval numbers.
- Task-type note from practice: Qwen-family models are a strong local default for vision/OCR/browser-agent tasks.

## Fine-tuning - the last resort for stubborn gaps

When a custom task has a capability-shaped gap that prompt optimization (Step 7) could not close, fine-tuning an open-weight model on your own examples is the remaining lever (HuggingFace ecosystem: datasets, TRL/LoRA-style tooling, hosted training).

- Only after Step 7 failed - a tuned prompt is cheaper than a tuned model by orders of magnitude.
- You need what the eval already forced you to build: a golden set. Scale it up (hundreds of examples, frontier-generated + human-validated) and it becomes the training set.
- Evaluate the fine-tune with the SAME harness and splits - a fine-tune is just another candidate row in the tradeoff table.

## Operate it honestly

- **Date-stamp the decision.** The verdict holds for these prompts, these providers, this month. Prices and models turn over monthly.
- **Re-run triggers:** the task prompt changes materially; the provider pin disappears or changes quant; a notable new open-weight model ships in the task's family; or quarterly, whichever comes first. The harness is resumable and costs ~$1 - re-running is cheap.
- **Monitor the deterministic guards in production** - guard-violation rate drifting up is the early signal that the provider changed something under you.
- Keep the frontier path one config flag away. The cheapest rollback is the one you never removed.
