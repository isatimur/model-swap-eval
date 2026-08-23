"""build_golden.py - Step 2: propose frontier-model references + assign dev/test/val splits.

Reads tasks.py (see schema below). For every case with NO "ref" key at all, calls the
FRONTIER model to PROPOSE a reference output. (An explicit "ref": None means intentionally
reference-free - boundary cases graded on dispersion, and subjective cases judged by the
blind panel - and gets no proposal.) Writes outputs/golden_proposed.json and STOPS:
a human must validate/edit the proposals (fix any the human disagrees with - first make the
frontier model agree with the human, THEN it becomes the oracle), and paste the approved
refs back into tasks.py (or keep golden_proposed.json as golden.json; grade.py falls back
to it). Nothing becomes a reference without human sign-off.

Also assigns deterministic dev/test/val splits (~50/30/20) to cases lacking a "split".

Usage:  OR_KEY=<openrouter-key> python3 build_golden.py [--n 3]
        --n : proposals per case (default 1; use 3 on subjective tasks to expose variance)

--- tasks.py schema (the contract shared by all templates in this skill) -------------------
FRONTIER = "<incumbent-model-id>"                # your current frontier model; verify the live id
                                                 # via pick_candidates.py - never trust a remembered version string
MODELS   = [FRONTIER, "<candidate-id>", ...]     # candidates + incumbent, all swept (shortlist LIVE, see candidate-selection.md)
SEEDS    = [11, 23, 42, 77, 101]                 # >=3, default 5
JUDGES   = ["x-ai/...", "openai/...", "google/..."]  # families != candidate families
BANNED   = ["i couldn't help but notice", ...]   # optional banned phrases for text tasks
MAX_COST_PER_CELL = 0.10                          # optional: run_sweep warns on any cell above this (reasoning-runaway guard)

TASKS = [{
  "task": "lead_scoring",
  "kind": "structured",            # structured | numeric | subjective
  "system": "<the exact production system prompt>",
  "max_tokens": 8000,
  "temperature": 0.0,              # the production value
  "rubric": "...{ctx}...",         # subjective only: judge rubric, {ctx} = case input
  "cases": [{
    "id": "hot_lead",
    "input": "<real production input>",
    "split": "dev",                # dev | test | val (assigned here if absent)
    "ref": {"tier": "Hot", "total": 89},   # structured: dict | numeric: number | subjective/
                                           # boundary: explicit None | key ABSENT: build_golden
                                           # proposes one via the frontier model
    "validated": True,             # REQUIRED on any tasks.py "ref": a human signed off on it.
                                   # grade.py refuses to grade against an unvalidated reference.
    "ref_fields": ["tier"],        # structured: fields graded exact-match (dot-paths ok). For a
                                   # boundary case (ref:None) this is OPTIONAL - grade.py reports
                                   # dispersion over every scalar field the models emit if omitted.
    "tolerance": {"total": 5},     # numeric fields graded |out - ref| <= tol
    "hard": False, "trap": False,  # mark boundary/trap cases (report them separately)
    "note": "",
    "fabrication_check": True,     # text tasks: flag numbers absent from the input
    "word_range": [40, 80],        # optional deterministic length guard
  }],
}]
--------------------------------------------------------------------------------------------
"""
import os, sys, json, time, random, urllib.request, urllib.error
from collections import Counter
from tasks import TASKS, FRONTIER, MODELS
MODEL_BY_ID = {m["id"]: m for m in MODELS}

KEY = os.environ["OR_KEY"]
URL = "https://openrouter.ai/api/v1/chat/completions"
OUT = "outputs/golden_proposed.json"
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 1

def call_openrouter(model, system, user, max_tokens, temperature, seed):
    body = json.dumps({"model": model,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "max_tokens": max_tokens, "temperature": temperature,
                       "seed": seed}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                r = json.load(resp)
            return r["choices"][0]["message"].get("content") or ""
        except Exception as e:
            if attempt == 3: raise
            print(f"    retry after {type(e).__name__}: {e}"); time.sleep(6 * (attempt + 1))

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/responses"


def extract_output_text(data):
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    chunks = []
    for item in data.get("output") or []:
        for c in item.get("content") or []:
            if isinstance(c.get("text"), str):
                chunks.append(c["text"])
    if not chunks:
        raise ValueError("no output text in Responses API reply")
    return "".join(chunks)


def call_openai_responses(model, instructions, input_obj, json_schema, timeout=6):
    body = {"model": model, "instructions": instructions, "input": json.dumps(input_obj),
            "text": {"format": {"type": "json_schema", "name": "eval_decision",
                                 "strict": True, "schema": json_schema}}}
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        data = json.load(resp)
    return extract_output_text(data)


def call_for_model(model_cfg, t, case, seed):
    provider = model_cfg.get("provider", "openrouter")
    if provider == "openrouter":
        return call_openrouter(model_cfg["id"], t["system"], case["input"], t["max_tokens"],
                                t["temperature"], seed)
    if provider == "openai_responses":
        return call_openai_responses(model_cfg["id"], t["system"], case["input"], t["json_schema"])
    raise ValueError(f'unknown provider {provider!r} for model {model_cfg["id"]!r}')

os.makedirs("outputs", exist_ok=True)
golden = json.load(open(OUT)) if os.path.exists(OUT) else {}   # resumable

# 1. split assignment - PRESERVE any prior assignment so adding a case never reshuffles existing
#    splits (that would contaminate the dev/test/val boundary the optimization loop depends on - M6).
#    New cases are assigned against the TASK'S RUNNING TOTAL, not just this batch: sizing the ratio
#    off len(fresh) alone starves val for any task grown a few cases at a time (e.g. two cases added
#    now, two more added later -> 50/30/20 of "2" rounds to dev/test every time, val never fires).
RATIO = {"dev": 0.5, "test": 0.3, "val": 0.2}
SPLITS_OUT = "outputs/splits.json"
prior = json.load(open(SPLITS_OUT)) if os.path.exists(SPLITS_OUT) else {}
rng = random.Random(2026)
for t in TASKS:
    for c in t["cases"]:                                    # honor an explicit split, else a persisted one
        c["split"] = c.get("split") or prior.get(f'{t["task"]}/{c["id"]}')
    fresh = [c for c in t["cases"] if not c.get("split")]   # only genuinely new cases get shuffled+assigned
    rng.shuffle(fresh)
    total = len(t["cases"])                                 # target ratios are of the WHOLE task, prior included
    counts = Counter(c["split"] for c in t["cases"] if c.get("split"))
    for c in fresh:                                          # greedily fill whichever split is furthest under quota
        split = max(RATIO, key=lambda s: RATIO[s] * total - counts.get(s, 0))
        c["split"] = split
        counts[split] += 1
# persist splits for EVERY case (grade.py + the optimization loop read this; cases with a ref in
# tasks.py would otherwise have no persisted split)
json.dump({f'{t["task"]}/{c["id"]}': c["split"] for t in TASKS for c in t["cases"]},
          open(SPLITS_OUT, "w"), indent=2)

# 2. frontier proposals for cases with no "ref" key ("ref": None = intentionally reference-free)
todo = [(t, c) for t in TASKS for c in t["cases"]
        if "ref" not in c and f'{t["task"]}/{c["id"]}' not in golden]
print(f"proposing references for {len(todo)} cases on {FRONTIER} (n={N} per case)")
for t, c in todo:
    key = f'{t["task"]}/{c["id"]}'
    # proposal seeds are disjoint from the sweep SEEDS: otherwise the frontier's identical sweep
    # cell (same model+seed) would trivially match its own reference by construction (M7).
    frontier_cfg = MODEL_BY_ID[FRONTIER]
    props = [call_for_model(frontier_cfg, t, c, 9001 + i) for i in range(N)]
    golden[key] = {"split": c["split"], "hard": c.get("hard", False),
                   "proposals": props, "ref_raw": props[0],
                   "human_validated": False}
    json.dump(golden, open(OUT, "w"), indent=2)                # save after every case
    print(f"  {key:40s} split={c['split']} -> {len(props)} proposal(s) saved")

# 3. split + hardness report
print("\nSPLITS:")
for t in TASKS:
    by = {}
    for c in t["cases"]: by.setdefault(c["split"], []).append(c["id"])
    hard = [c["id"] for c in t["cases"] if c.get("hard") or c.get("trap")]
    print(f'  {t["task"]}: ' + " | ".join(f"{s}={len(v)}" for s, v in sorted(by.items())))
    if not hard:
        print(f'    WARNING: no hard/trap cases in {t["task"]} - easy-only golden sets '
              f'over-claim "solved". Add boundary + false-trigger-trap cases before sweeping.')

print(f"""
NEXT (human step - do not skip):
  1. Review {OUT}. For each case: does the frontier proposal match YOUR judgment?
     If not, fix the prompt or the reference until human and frontier agree.
  2. Paste approved refs into tasks.py as "ref" (structured: parse the JSON; subjective:
     leave ref=None - quality is judged by the blind panel, the frontier is a sweep baseline).
  3. Mark each reference as human-signed-off, or grade.py REFUSES it:
       - a "ref" pasted into tasks.py  -> add "validated": true to that case
       - a ref left in {OUT} as the fallback -> set "human_validated": true on that entry
  (splits are auto-saved to outputs/splits.json for ALL cases - grade.py reads them; no hand-copying.)
""")
