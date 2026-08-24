"""run_sweep.py - Step 4: N-seed, provider-PINNED, concurrent, resumable sweep.

Runs every model in tasks.py MODELS (candidates + the incumbent FRONTIER) over every
task x case x seed. Pinning keeps quantization constant per model (rigor.md #3); the
frontier's own seed variance is the eval's noise floor and its measured $/call anchors
the savings math. Records content + cost + latency + the provider that ACTUALLY served
each call. Incremental save; re-running resumes from outputs/seeds_raw.json.

Usage:  OR_KEY=<key> python3 run_sweep.py
Needs:  tasks.py (schema in build_golden.py docstring), provider_pins.json (pick_candidates.py pin)
Output: outputs/seeds_raw.json - one record per task x case x model x seed
"""
import os, json, time, hashlib, statistics, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from tasks import TASKS, MODELS, SEEDS
MODEL_IDS = [m["id"] for m in MODELS]
MODEL_BY_ID = {m["id"]: m for m in MODELS}
try:
    from tasks import MAX_COST_PER_CELL          # optional hard-ceiling warn; None = median-based only
except ImportError:
    MAX_COST_PER_CELL = None

PROVIDERS_USED = {m.get("provider", "openrouter") for m in MODELS}   # only demand the keys actually needed
KEY = None
if "openrouter" in PROVIDERS_USED:
    KEY = os.environ.get("OR_KEY")
    if not KEY:
        raise SystemExit('OR_KEY is not set, but MODELS contains model(s) with provider "openrouter" '
                         '- export your OpenRouter key, or drop those models from MODELS.')
URL = "https://openrouter.ai/api/v1/chat/completions"
WORKERS = 5
OUT = "outputs/seeds_raw.json"
PINS = json.load(open("provider_pins.json")) if os.path.exists("provider_pins.json") else {}

def prompt_hash(t, case):                        # fingerprints what actually went into the call, so editing
    payload = json.dumps([t["system"], case["input"], t["max_tokens"], t["temperature"],
                           t.get("json_schema")], sort_keys=True)  # a task's prompt/input/params invalidates cached cells
    return hashlib.sha1(payload.encode()).hexdigest()[:12]

EXPECTED_HASH = {(t["task"], c["id"]): prompt_hash(t, c) for t in TASKS for c in t["cases"]}

def call_openrouter(model, system, user, max_tokens, temperature, seed):
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature, "seed": seed}
    pin = PINS.get(model)
    if pin:  # constant quantization for this model across the whole sweep
        body["provider"] = {"order": [pin["provider"]], "allow_fallbacks": False}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        r = json.load(resp)
    dt = time.time() - t0
    msg = r["choices"][0]["message"]
    usage = r.get("usage", {})
    return {
        "content": msg.get("content") or "",
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cost": usage.get("cost", 0.0),
        "latency_s": round(dt, 1),
        "finish_reason": r["choices"][0].get("finish_reason"),
        "provider": r.get("provider"),            # verify the pin held
        "served_model": r.get("model"),
        "pinned_provider": pin["provider"] if pin else None,
        "pinned_quant": pin["quant"] if pin else None,
    }

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
if "openai_responses" in PROVIDERS_USED and not OPENAI_KEY:   # fail fast, never send "Bearer None"
    raise SystemExit('OPENAI_API_KEY is not set, but MODELS contains model(s) with provider '
                     '"openai_responses" - export your OpenAI key before sweeping.')
OPENAI_URL = "https://api.openai.com/v1/responses"


def extract_output_text(data):          # mirrors gate.mjs's extractOutputText() exactly
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


try:
    from tasks import PRICING            # optional: {"model-id": {"prompt": $/tok, "completion": $/tok}}
except ImportError:
    PRICING = {}


_PRICING_WARNED = set()                # loud-once-per-model, not once-per-cell
_PRICING_LOCK = threading.Lock()


def priced_cost(model, usage_input, usage_output):
    p = PRICING.get(model)
    if not p:                          # unknown cost, NOT zero cost - never coerce this to 0 downstream
        with _PRICING_LOCK:
            first = model not in _PRICING_WARNED
            _PRICING_WARNED.add(model)
        if first:
            print(f"WARN: no PRICING entry for {model} - cost will be reported as n/a, not $0. "
                  f"Add PRICING[{model!r}] = {{'prompt': $/tok, 'completion': $/tok}} to tasks.py "
                  f"to get real $/call and savings numbers for this model.")
        return None
    return usage_input * p.get("prompt", 0) + usage_output * p.get("completion", 0)


def _build_openai_responses_body(model, instructions, input_obj, json_schema):
    return {
        "model": model,
        "instructions": instructions,
        "input": json.dumps(input_obj),
        "text": {"format": {"type": "json_schema", "name": "eval_decision",
                             "strict": True, "schema": json_schema}},
    }


def call_openai_responses(model, instructions, input_obj, json_schema, timeout=6):
    body = _build_openai_responses_body(model, instructions, input_obj, json_schema)
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        data = json.load(resp)
    dt = time.time() - t0
    raw = extract_output_text(data)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None                    # strict mode should prevent this; never trust blindly
    usage = data.get("usage") or {}
    return {
        "content": raw,
        "content_json": parsed,
        "reasoning_tokens": 0,
        "completion_tokens": usage.get("output_tokens", 0),
        "cost": priced_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        "latency_s": round(dt, 1),
        "finish_reason": data.get("status"),
        "provider": "openai",
        "served_model": data.get("model", model),
        "pinned_provider": None,
        "pinned_quant": None,
        "usage_input_tokens": usage.get("input_tokens", 0),
        "usage_output_tokens": usage.get("output_tokens", 0),
    }


def call_for_model(model_cfg, t, case, seed):
    provider = model_cfg.get("provider", "openrouter")
    if provider == "openrouter":
        return call_openrouter(model_cfg["id"], t["system"], case["input"], t["max_tokens"],
                                t["temperature"], seed)
    if provider == "openai_responses":
        return call_openai_responses(model_cfg["id"], t["system"], case["input"], t["json_schema"])
    raise ValueError(f'unknown provider {provider!r} for model {model_cfg["id"]!r}')


def call_retry(*a):
    delay = 8
    for attempt in range(5):
        try:
            return call_for_model(*a)
        except urllib.error.HTTPError as e:
            if e.code in (402, 429, 500, 502, 503) and attempt < 4:
                time.sleep(delay); delay *= 2; continue
            if attempt < 4: time.sleep(4); continue
            raise
        except Exception:
            if attempt < 4: time.sleep(4); continue
            raise
    raise RuntimeError("unreachable")

# resume support
os.makedirs("outputs", exist_ok=True)
done = {}
skipped_errors = stale = 0
if os.path.exists(OUT):
    for r in json.load(open(OUT)):
        if r.get("error"):                        # errored cells (429/500/timeout) are NOT done - retry them
            skipped_errors += 1
            continue
        expected = EXPECTED_HASH.get((r["task"], r["case"]))
        stored = r.get("prompt_hash")              # missing on pre-hash data: trust it (no forced re-sweep)
        if stored and expected and stored != expected:
            stale += 1                             # task/case prompt or params changed since this cell ran
            continue
        done[(r["task"], r["case"], r["model"], r["seed"])] = r
    note = []
    if skipped_errors: note.append(f"{skipped_errors} errored cell(s) will be retried")
    if stale: note.append(f"{stale} stale cell(s) (prompt/params changed) will be re-run")
    print(f"resuming: {len(done)} cells already present" + (f" ({'; '.join(note)})" if note else ""))

jobs = []
for t in TASKS:
    for case in t["cases"]:
        for model_cfg in MODELS:
            for seed in SEEDS:
                if (t["task"], case["id"], model_cfg["id"], seed) not in done:
                    jobs.append((t, case, model_cfg, seed))

n_cases = sum(len(t["cases"]) for t in TASKS)
print(f"{len(jobs)} cells to run ({len(MODELS)} models x {n_cases} cases x {len(SEEDS)} seeds)")
unpinned = [mid for mid in MODEL_IDS if mid not in PINS and "/" in mid
            and MODEL_BY_ID[mid].get("provider", "openrouter") == "openrouter"]
if unpinned:
    print(f"note: unpinned (closed/first-party or missing pin): {unpinned}")

results = list(done.values())
count = [0]
model_costs = {}    # per-model cost history for runaway detection (global median is meaningless across 90x cost spread)

def work(job):
    t, case, model_cfg, seed = job
    ph = EXPECTED_HASH[(t["task"], case["id"])]
    try:
        out = call_retry(model_cfg, t, case, seed)
        return {"task": t["task"], "case": case["id"], "model": model_cfg["id"], "seed": seed,
                "prompt_hash": ph, **out}
    except Exception as e:
        return {"task": t["task"], "case": case["id"], "model": model_cfg["id"], "seed": seed,
                "prompt_hash": ph, "error": str(e), "content": ""}

with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(work, j): j for j in jobs}
    for fut in as_completed(futs):
        rec = fut.result()
        results.append(rec)
        count[0] += 1
        empty = "" if rec.get("content", "").strip() else " <EMPTY!>"       # empties are data, not retries
        err = f' ERR {rec["error"][:40]}' if rec.get("error") else ""
        pin_broke = (rec.get("pinned_provider") and rec.get("provider")
                     and rec["pinned_provider"] not in str(rec["provider"])) and " <PIN-MISS!>" or ""
        tag = f'{rec["task"]}/{rec["case"]}/{rec["model"].split("/")[-1]}#{rec["seed"]}'
        cost_str = "$n/a  " if rec.get("cost") is None else f'${rec["cost"]:.4f}'   # unknown != $0
        print(f'[{count[0]:4d}/{len(jobs)}] {tag:58s} {rec.get("latency_s","?"):>5}s '
              f'ct={rec.get("completion_tokens","?"):>5} {cost_str} '
              f'[{rec.get("provider","?")}]{pin_broke}{empty}{err}')
        c_cost = rec.get("cost")                                              # reasoning-runaway watch (per model)
        if c_cost is not None:                                                # unpriced cell: no cost signal to watch
            mc = model_costs.setdefault(rec["model"], [])
            mc.append(c_cost)
            if MAX_COST_PER_CELL and c_cost > MAX_COST_PER_CELL:              # independent of the median check
                print(f'      ^ WARN: cell ${c_cost:.4f} exceeds MAX_COST_PER_CELL ${MAX_COST_PER_CELL} '
                      f'(rtok={rec.get("reasoning_tokens")}, {rec.get("latency_s")}s)')
            if len(mc) >= 6:                                                  # this MODEL's own median, not the global one
                med = statistics.median(mc)
                if med > 0 and c_cost > 8 * med:
                    print(f'      ^ WARN: possible reasoning runaway - ${c_cost:.4f} = {c_cost/med:.0f}x this model\'s median '
                          f'(rtok={rec.get("reasoning_tokens")}, {rec.get("latency_s")}s). max_tokens enforcement is provider-dependent.')
        if count[0] % 10 == 0:
            json.dump(results, open(OUT, "w"), indent=2)

json.dump(results, open(OUT, "w"), indent=2)
priced = [r for r in results if r.get("cost") is not None]
unpriced = len(results) - len(priced)                       # cells with unknown cost - NOT $0 cells
total = sum(r["cost"] for r in priced)
empties = sum(1 for r in results if not r.get("content", "").strip())
errs = sum(1 for r in results if r.get("error"))
pin_misses = sum(1 for r in results if r.get("pinned_provider") and r.get("provider")
                 and r["pinned_provider"] not in str(r["provider"]))
print(f"\nDONE  {len(results)} cells  empties={empties}  errors={errs}  "
      f"pin_misses={pin_misses}  total_cost=${total:.4f}"
      + (f" over {len(priced)} priced cell(s)" if unpriced else ""))
if unpriced:
    unpriced_models = sorted({r["model"] for r in results if r.get("cost") is None})
    print(f"WARNING: {unpriced} cell(s) have UNKNOWN cost (no PRICING entry): {unpriced_models}. "
          f"That is not $0 - grade.py reports those models' $/call and savings as n/a.")
if pin_misses:
    print("WARNING: some calls were served off-pin - those cells mix quantizations; investigate before grading.")
top = sorted(priced, key=lambda r: -r["cost"])[:3]                            # spot budget-eating cells
if top and top[0]["cost"] > 0:
    print("most expensive cells (watch for reasoning runaways inflating measured $/call):")
    for r in top:
        print(f'  ${r["cost"]:.4f}  {r["task"]}/{r["case"]}/{r["model"].split("/")[-1]}#{r["seed"]}  '
              f'rtok={r.get("reasoning_tokens")}  {r.get("latency_s")}s')
