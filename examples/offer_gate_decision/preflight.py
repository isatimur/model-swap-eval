"""preflight.py - catch mistakes and cost surprises BEFORE any sweep spends money.

Two independent subcommands:

  validate : pure offline schema check on tasks.py - no network, no OR_KEY needed.
             Every other template discovers a malformed tasks.py the hard way, mid-run
             (or worse, grade.py refuses a whole task after the sweep already spent money
             on it). This front-loads those checks: duplicate ids, missing "validated" on
             a pasted ref, bad "kind", a subjective task with no usable rubric, too few
             judges/seeds, judge-family overlap, malformed tolerance/word_range. Prints
             every problem found (not just the first) and exits non-zero on anything fatal.

  estimate : needs OR_KEY. Fetches live per-model pricing and computes the WORST-CASE
             cost ceiling for the full sweep the same way OpenRouter itself pre-authorizes
             a call (prompt tokens x prompt price + max_tokens x completion price, per
             cell) - this is deliberately pessimistic, not a prediction; real spend is
             usually far below it (the reference harness: ~$1.15 for 9 models x 8 cases x
             5 seeds + a judge panel). Then checks the key's live remaining credit against
             that ceiling and warns if the account looks too thin - a thin account silently
             biases run_sweep.py toward cheap models because pricier ones 402 first
             (candidate-selection.md's "practical gotchas").

Usage:  python3 preflight.py validate
        OR_KEY=<key> python3 preflight.py estimate
"""
import os, sys, json, urllib.request
from tasks import TASKS, MODELS, SEEDS, FRONTIER
try:
    from tasks import JUDGES
except ImportError:
    JUDGES = []

VALID_KINDS = {"structured", "numeric", "subjective"}


# ---------------------------------------------------------------------- validate
def validate():
    errors, warnings = [], []

    def err(msg): errors.append(msg)
    def warn(msg): warnings.append(msg)

    model_ids = []
    for i, m in enumerate(MODELS):
        if not isinstance(m, dict) or "id" not in m:
            err(f"MODELS[{i}] must be a dict with an \"id\" key, got {m!r}")
            continue
        model_ids.append(m["id"])
        provider = m.get("provider", "openrouter")
        if provider not in ("openrouter", "openai_responses"):
            err(f'MODELS[{i}] ("{m["id"]}"): provider="{provider}" - must be "openrouter" or "openai_responses"')

    if FRONTIER not in model_ids:
        err(f'FRONTIER "{FRONTIER}" is not in MODELS - the incumbent must be swept too (rigor.md #1).')
    if len(model_ids) != len(set(model_ids)):
        err(f"MODELS has duplicate ids: {[i for i in model_ids if model_ids.count(i) > 1]}")
    non_frontier = [i for i in model_ids if i != FRONTIER]
    if not (3 <= len(non_frontier) <= 8):
        warn(f"{len(non_frontier)} non-frontier candidate(s) in MODELS - candidate-selection.md "
             f"recommends 4-8 spanning the price range (below 4 you learn too little).")
    if len(SEEDS) < 3:
        err(f"SEEDS has only {len(SEEDS)} - rigor.md #1 mandates >= 3, default 5. Single/double-seed "
            f"rankings are noise.")
    elif len(SEEDS) < 5:
        warn(f"SEEDS has {len(SEEDS)} - the default is 5; fine if deliberate, but cutting seeds to "
             f"save cents is exactly what rigor.md #1 warns against.")
    if len(SEEDS) != len(set(SEEDS)):
        err(f"SEEDS has duplicate values: {SEEDS}")
    if not TASKS:
        err("TASKS is empty - nothing to evaluate.")

    task_names = [t.get("task") for t in TASKS]
    if len(task_names) != len(set(task_names)):
        err(f"duplicate task names: {[n for n in task_names if task_names.count(n) > 1]}")

    needs_judges = False
    for t in TASKS:
        tname = t.get("task", "<unnamed>")
        for key in ("task", "kind", "system", "max_tokens", "temperature", "cases"):
            if key not in t:
                err(f'{tname}: missing required key "{key}"')
        kind = t.get("kind")
        if kind not in VALID_KINDS:
            err(f'{tname}: kind="{kind}" - must be one of {sorted(VALID_KINDS)}')
        models_by_id = {m["id"]: m for m in MODELS if isinstance(m, dict) and "id" in m}
        task_model_providers = {models_by_id[mid].get("provider", "openrouter") for mid in model_ids if mid in models_by_id}
        if len(task_model_providers) > 1:
            warn(f"{tname}: MODELS mixes providers ({sorted(task_model_providers)}) - fine if "
                 f"deliberate (comparing across calling patterns), but confirm that's the intent.")
        if "openai_responses" in task_model_providers:
            if "json_schema" not in t:
                err(f'{tname}: at least one model uses provider "openai_responses" but the task has '
                    f'no "json_schema" - required for strict-mode structured output.')
        cases = t.get("cases") or []
        if not cases:
            err(f"{tname}: no cases")
        elif not (5 <= len(cases) <= 30):
            warn(f"{tname}: {len(cases)} case(s) - methodology.md suggests ~10-20 per task.")
        if kind == "subjective":
            needs_judges = True
            rubric = t.get("rubric")
            if not rubric:
                err(f"{tname}: subjective task has no \"rubric\"")
            elif "{ctx}" not in rubric:
                err(f'{tname}: rubric does not contain the "{{ctx}}" placeholder for the case input')

        ids = [c.get("id") for c in cases]
        if len(ids) != len(set(ids)):
            err(f"{tname}: duplicate case id(s): {[i for i in ids if ids.count(i) > 1]}")
        has_hard_or_trap = False
        for c in cases:
            cid = c.get("id", "<no id>")
            tag = f"{tname}/{cid}"
            if "id" not in c:
                err(f"{tname}: a case is missing \"id\"")
            if "input" not in c or not c.get("input"):
                err(f"{tag}: missing or empty \"input\"")
            if "openai_responses" in task_model_providers and not isinstance(c.get("input"), dict):
                err(f'{tag}: task uses provider "openai_responses" - case "input" must be a dict '
                    f'(the JSON payload), got {type(c.get("input")).__name__}')
            if task_model_providers == {"openrouter"} and isinstance(c.get("input"), dict):
                err(f'{tag}: task uses only provider "openrouter" - case "input" must be a string '
                    f'prompt, got a dict')
            if c.get("hard") or c.get("trap"):
                has_hard_or_trap = True
            if "ref" in c and c["ref"] is not None and kind in ("structured", "numeric"):
                if not c.get("validated"):
                    err(f'{tag}: has a "ref" but no "validated": true - grade.py will REFUSE this '
                        f"(a human must sign off before it becomes an oracle).")
            if "ref_fields" in c and not isinstance(c["ref_fields"], list):
                err(f'{tag}: "ref_fields" must be a list, got {type(c["ref_fields"]).__name__}')
            tol = c.get("tolerance")
            if tol is not None:
                if kind == "structured" and not isinstance(tol, dict):
                    err(f'{tag}: structured "tolerance" must be a dict of field->number, got '
                        f"{type(tol).__name__}")
                if kind == "numeric" and not isinstance(tol, (int, float)):
                    err(f'{tag}: numeric "tolerance" must be a number, got {type(tol).__name__}')
            wr = c.get("word_range")
            if wr is not None:
                if not (isinstance(wr, (list, tuple)) and len(wr) == 2
                        and all(isinstance(x, (int, float)) for x in wr) and wr[0] <= wr[1]):
                    err(f'{tag}: "word_range" must be a [low, high] pair with low <= high, got {wr!r}')
        if not has_hard_or_trap:
            warn(f'{tname}: no hard/trap cases - rigor.md #5: "easy-only golden sets over-claim solved."')

    if needs_judges:
        if len(JUDGES) < 3:
            err(f"a subjective task exists but JUDGES has only {len(JUDGES)} entr(y/ies) - rigor.md #6 "
                f"mandates >= 3.")
        overlap = {j.split("/")[0] for j in JUDGES} & {i.split("/")[0] for i in model_ids}
        if overlap:
            warn(f"judge family overlaps a candidate family: {overlap} - same-family judges favor "
                 f"their siblings' style (rigor.md #6); judge.py will warn again at run time.")

    print(f"preflight validate: {len(TASKS)} task(s), {len(MODELS)} model(s), {len(SEEDS)} seed(s)\n")
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    if errors:
        print("REFUSING to proceed - fix the errors above before running build_golden.py / run_sweep.py.")
        sys.exit(1)
    print("tasks.py looks structurally sound. Warnings (if any) are judgment calls, not blockers.")


# ---------------------------------------------------------------------- estimate
BASE = "https://openrouter.ai/api/v1"


def get(path, key):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def price(m, kind):  # $ per token (pricing API reports $/token, not $/M)
    try: return float(m["pricing"][kind])
    except (KeyError, TypeError, ValueError): return None


def est_tokens(text):  # ~4 chars/token - a rough, documented approximation, not a tokenizer
    return max(1, len(text) // 4)


def estimate():
    key = os.environ["OR_KEY"]
    catalog = {m["id"]: m for m in get("/models", key)["data"]}
    n_seeds = len(SEEDS)
    ceiling = 0.0
    missing_pricing = []
    print(f"{'model':42s} {'cells':>7s} {'ceiling $':>10s}")
    per_model = {}
    for model in MODELS:
        n_cells = sum(len(t["cases"]) for t in TASKS) * n_seeds
        m = catalog.get(model)
        if not m:
            missing_pricing.append(model)
            continue
        p_in, p_out = price(m, "prompt"), price(m, "completion")
        if p_in is None or p_out is None:
            missing_pricing.append(model)
            continue
        model_ceiling = 0.0
        for t in TASKS:
            for case in t["cases"]:
                prompt_toks = est_tokens(t["system"]) + est_tokens(case["input"])
                model_ceiling += n_seeds * (p_in * prompt_toks + p_out * t["max_tokens"])
        per_model[model] = model_ceiling
        ceiling += model_ceiling
        print(f"{model:42s} {n_cells:7d} {model_ceiling:10.4f}")
    if missing_pricing:
        print(f"\nno live pricing found for: {missing_pricing} (typo, or a closed model not listed by "
              f"/models the same way) - not included in the ceiling above.")
    print(f"\nWORST-CASE ceiling for the full sweep: ${ceiling:.2f}")
    print("This is deliberately pessimistic (full max_tokens billed on every call, at completion price,")
    print("the same way OpenRouter pre-authorizes it) - not a prediction. The reference harness (9 models")
    print("x 8 cases x 5 seeds + a judge panel) actually cost ~$1.15. Prompt-token counts are a ~4-char/")
    print("token estimate, not a real tokenizer - treat the ceiling as directional, not exact.")

    try:
        cred = get("/key", key)
        data = cred.get("data", cred)
        remaining = data.get("limit_remaining")
        if remaining is not None and remaining < ceiling:
            print(f"\nWARNING: live remaining credit (${remaining:.2f}) is LESS than the worst-case "
                  f"ceiling (${ceiling:.2f}). A thin account 402s on pricier models before cheap ones, "
                  f"silently biasing the sweep - load more credit first (candidate-selection.md).")
        elif remaining is not None:
            print(f"\nlive remaining credit: ${remaining:.2f} - covers the worst-case ceiling.")
    except Exception as e:
        print(f"\n(could not check remaining credit: {type(e).__name__}: {e})")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if mode == "validate":
        validate()
    elif mode == "estimate":
        estimate()
    else:
        sys.exit(f"unknown mode {mode!r} - use 'validate' or 'estimate'")
