"""grade.py - Step 5: deterministic grading + cost-savings / accuracy tradeoff table.

Grades outputs/seeds_raw.json against the human-validated references, per task kind:
  structured : JSON extraction -> exact-match on ref_fields + |x-ref|<=tolerance on numeric
               fields; invalid JSON = full miss. Boundary cases (explicit ref=None) -> dispersion
               report. hard/trap cases get their own lines (rigor.md #5).
  numeric    : first number in output vs ref -> MAE + within-tolerance rate.
  subjective : deterministic guards only (fabrication = metric-like numbers absent from the
               input, banned phrases, word_range); quality itself comes from judge.py.

Honesty (matches this skill's own rules):
  - Refuses any reference that is not human-validated (a tasks.py "ref" needs "validated": true;
    a golden_proposed.json fallback needs "human_validated": true). rigor: unvalidated oracle = no verdict.
  - Aggregates pass-rates at the CASE level (cluster-robust), because at temp 0 the per-seed
    repeats are near-replicates, not independent trials - effective n = number of cases, not cells.
  - Prints the minimum difference detectable at this n; "tied" means NOT SEPARABLE at this n,
    which is absence of evidence, not proof of equivalence.
  - A cell with an UNKNOWN cost (cost=None: e.g. a provider whose model has no tasks.py PRICING
    entry) is never counted as $0. Any such cell makes that model's $/call and savings read "n/a"
    (null in grade_agg.json). A fabricated "$0.0000/call, 100% saved" is the exact business number
    this tool exists to get right.

Usage:  python3 grade.py            (no API calls - pure local computation)
Output: prints the console report; also writes outputs/grade_agg.json (structured mirror of the
        deterministic pass-rate + cost/latency sections) for templates/build_report.py to consume.
"""
import os, json, re, math, statistics
from collections import Counter, defaultdict
from tasks import TASKS, MODELS, FRONTIER
try:
    from tasks import BANNED
except ImportError:
    BANNED = []

RAW = json.load(open("outputs/seeds_raw.json"))
GOLDEN = json.load(open("outputs/golden_proposed.json")) if os.path.exists("outputs/golden_proposed.json") else {}
SPLITS = json.load(open("outputs/splits.json")) if os.path.exists("outputs/splits.json") else {}
MODEL_IDS = [m["id"] for m in MODELS]
MODELS_ORDER = [mid for mid in MODEL_IDS if any(r["model"] == mid for r in RAW)]

def balanced_objects(text):                   # every top-level {...} span, in order
    objs, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0: objs.append(text[start:i + 1])
    return objs

def extract_json(text):                       # LAST parseable object (reasoning traces precede the answer)
    for s in reversed(balanced_objects(text or "")):
        try: return json.loads(s)
        except Exception: continue
    return None

def extract_json_or_structured(r):             # prefer an already-parsed, schema-guaranteed result
    if r.get("content_json") is not None:       # (openai_responses: strict mode already validated it)
        return r["content_json"]
    return extract_json(r.get("content", ""))   # openrouter: fall back to reasoning-trace extraction

def get_path(obj, path):                       # dotted-path getter: "fit.subtotal"
    for part in path.split("."):
        if not isinstance(obj, dict): return None
        obj = obj.get(part)
    return obj

def metric_tokens(text):                       # %-values, multipliers, 2+ digit integers
    return set(re.findall(r"\d+\.?\d*\s?%|\d+\.?\d*x|\b\d{2,}\b", text.lower()))

def ref_for(task, case):
    key = f'{task["task"]}/{case["id"]}'
    if "ref" in case:                          # explicit in tasks.py
        if case["ref"] is None:                # intentionally reference-free (boundary / subjective)
            return None
        if not case.get("validated"):          # M1: a pasted ref must carry a human sign-off
            raise SystemExit(f'REFUSING: ref for {key} in tasks.py lacks "validated": true - a human '
                             f'must sign off before it is used as an oracle (build_golden.py NEXT step).')
        return case["ref"]
    g = GOLDEN.get(key)                         # no "ref" key: must come from a validated golden proposal
    if not g:
        if task["kind"] in ("structured", "numeric"):
            raise SystemExit(f'REFUSING: {key} has no reference and no golden proposal - run build_golden.py '
                             f'(or set "ref": None if this is intentionally a boundary case).')
        return None                            # subjective with no ref: judged by the panel
    if not g.get("human_validated"):
        raise SystemExit(f'REFUSING: golden ref for {key} is not human_validated - validate it first (build_golden.py).')
    if task["kind"] == "structured":
        return extract_json(g["ref_raw"])
    if task["kind"] == "numeric":                 # parse the number out of the frontier's raw completion -
        return first_num(g["ref_raw"])            # a bare string always fails the numeric isinstance check below
    return g["ref_raw"]

def cells(task, case_id, model):               # gradeable records only (drop network-error cells - M10)
    return [r for r in RAW if r["task"] == task and r["case"] == case_id
            and r["model"] == model and not r.get("error")]

def first_num(text):
    m = re.search(r"-?\d+\.?\d*", text)
    return float(m.group(0)) if m else None

case_fracs = defaultdict(list)                 # model -> [pass fraction per case]  (case-level unit, M2)
errcells = defaultdict(int)
print("=" * 96)
for t in TASKS:
    print(f'\nTASK {t["task"]}  (kind={t["kind"]})')
    print("=" * 96)
    for case in t["cases"]:
        ref = ref_for(t, case)
        tag = " HARD" if case.get("hard") else (" TRAP" if case.get("trap") else "")
        split = case.get("split") or SPLITS.get(f'{t["task"]}/{case["id"]}', "?")
        print(f'\n--- {case["id"]}  (split={split}{tag})' + (f'  {case["note"]}' if case.get("note") else ""))
        for m in MODELS_ORDER:
            recs = cells(t["task"], case["id"], m)
            errcells[m] += sum(1 for r in RAW if r["task"] == t["task"] and r["case"] == case["id"]
                               and r["model"] == m and r.get("error"))
            if not recs: continue
            name = m.split("/")[-1][:26]
            if t["kind"] == "structured":
                vals = [extract_json_or_structured(r) for r in recs]
                parsed = sum(v is not None for v in vals)
                if ref is None:                # boundary case: report spread, don't pass/fail
                    fields = case.get("ref_fields", []) + list(case.get("tolerance", {}))
                    if not fields:             # none declared: every top-level scalar the models emitted
                        fields = sorted({k for v in vals if v for k, x in v.items()
                                         if isinstance(x, (str, int, float, bool))})
                    spread = {f: dict(Counter(str(get_path(v, f)) for v in vals if v)) for f in fields}
                    print(f'  {name:26s} boundary spread (parsed {parsed}/{len(recs)}): {spread or "(no JSON parsed)"}')
                    continue
                ok = 0
                for j in vals:
                    good = j is not None
                    for f in case.get("ref_fields", []):
                        good = good and get_path(j, f) == get_path(ref, f)
                    for f, tol in case.get("tolerance", {}).items():
                        rv, jv = get_path(ref, f), get_path(j, f) if j else None
                        good = good and isinstance(jv, (int, float)) and rv is not None and abs(jv - rv) <= tol
                    ok += good
                case_fracs[m].append(ok / len(recs))
                print(f'  {name:26s} pass {ok}/{len(recs)}  (parsed {parsed}/{len(recs)})')
            elif t["kind"] == "numeric":
                if not isinstance(ref, (int, float)):
                    print(f'  {name:26s} SKIP: numeric case has no numeric ref'); continue
                nums = [first_num(r.get("content", "")) for r in recs]
                errs = [abs(x - ref) for x in nums if x is not None]
                tol = case.get("tolerance") if isinstance(case.get("tolerance"), (int, float)) else 0
                ok = sum(1 for e in errs if e <= tol)
                case_fracs[m].append(ok / len(recs))
                mae = statistics.mean(errs) if errs else float("nan")
                print(f'  {name:26s} MAE={mae:.2f}  within +/-{tol}: {ok}/{len(recs)}  unparsed={len(recs)-len(errs)}')
            else:                              # subjective: deterministic guards only
                innum = metric_tokens(case["input"])
                nf = nb = nw = 0; ex = ""
                for r in recs:
                    c = r.get("content", "")
                    fab = ({x for x in metric_tokens(c) if x not in innum} if case.get("fabrication_check") else set())
                    lo, hi = case.get("word_range", [0, 10**9])
                    nf += bool(fab); nb += any(p in c.lower() for p in BANNED)
                    nw += not lo <= len(re.findall(r"\S+", c)) <= hi
                    ex = ex or (sorted(fab)[0] if fab else "")
                print(f'  {name:26s} fabricated_metric={nf}/{len(recs)}{" e.g." + ex if ex else ""}  '
                      f'banned={nb}/{len(recs)}  len_violations={nw}/{len(recs)}')

def nan_to_none(x):                            # NaN isn't valid JSON; null is the honest "n/a"
    return None if isinstance(x, float) and x != x else x

det_agg = None
cost_agg = None

# ---- CASE-CLUSTERED pass-rate: unit = case, not cell (temp-0 seeds are near-replicates, M2/M3) ----
gradeable = {m: fr for m, fr in case_fracs.items() if fr}
if gradeable:
    print("\n" + "=" * 96)
    kmax = max(len(fr) for fr in gradeable.values())
    print(f"DETERMINISTIC PASS-RATE  (case-clustered; unit = case, n_cases={kmax}; seeds are within-case replicates)")
    print("=" * 96)
    stats = {}
    for m in MODELS_ORDER:
        fr = gradeable.get(m)
        if fr:
            k = len(fr)
            se = statistics.stdev(fr) / math.sqrt(k) if k > 1 else float("nan")
            stats[m] = (statistics.mean(fr), se, k)
    best = max(stats, key=lambda m: stats[m][0])
    ses = [s[1] for s in stats.values() if s[1] == s[1]]
    pooled = statistics.mean(ses) if ses else float("nan")
    mdd = 1.96 * math.sqrt(2) * pooled if pooled == pooled else float("nan")
    det_agg = {"n_cases": kmax, "mdd_pp": nan_to_none(mdd * 100 if mdd == mdd else float("nan")), "models": {}}
    for m in sorted(stats, key=lambda m: -stats[m][0]):
        rate, se, k = stats[m]
        bse = stats[best][1]
        denom = math.sqrt((0 if bse != bse else bse**2) + (0 if se != se else se**2))
        z = (stats[best][0] - rate) / denom if denom > 0 else 0.0
        verdict = "not separable from best (tied != equivalent)" if abs(z) <= 1.96 else "CLEARLY WORSE"
        se_str = f"{se*100:4.1f}pp" if se == se else " n/a "
        star = " <- frontier baseline" if m == FRONTIER else ""
        print(f'  {m.split("/")[-1]:26s} {rate*100:5.1f}% +/- {se_str} (n_cases={k})  z_vs_best={z:4.2f}  {verdict}{star}')
        det_agg["models"][m] = {"pass_rate": rate, "se": nan_to_none(se), "n_cases": k,
                                 "z_vs_best": z, "verdict": verdict, "is_frontier": m == FRONTIER}
    if mdd == mdd:
        print(f'\n  Minimum difference detectable at n_cases={kmax}: ~{mdd*100:.0f}pp. '
              f'Smaller gaps are UNDETECTABLE here - "tied" = insufficient evidence, not equivalence.')
    print('  Add cases (not seeds) to narrow this. A tie at small n never justifies "model A = model B".')

# ---- cost & latency with error bars, savings anchored to the MEASURED frontier $/call (M14) ----
print("\n" + "=" * 96)
print("COST & LATENCY (per call, measured - includes reasoning-token burn; mean +/- stderr, p90 latency)")
print("=" * 96)
def p90(xs): return sorted(xs)[min(len(xs) - 1, int(math.ceil(0.9 * len(xs)) - 1))] if xs else float("nan")
costs = defaultdict(list); lats = defaultdict(list); rtoks = defaultdict(list); empties = defaultdict(int)
unpriced = defaultdict(int); ncells = defaultdict(int)
for r in RAW:
    if r.get("error"): continue                # M10: error cells excluded from cost math
    m = r["model"]
    ncells[m] += 1
    c = r.get("cost")                          # None (or absent) = cost UNKNOWN, not $0 - never coerce it
    if c is None: unpriced[m] += 1
    else: costs[m].append(c)
    lats[m].append(r.get("latency_s", 0) or 0)
    rtoks[m].append(r.get("reasoning_tokens", 0) or 0)
    empties[m] += 0 if r.get("content", "").strip() else 1

def mean_cost(m):                              # None = "we do not know", which is NOT the same as 0.0
    if unpriced[m] or not costs.get(m):        # ANY unknown cell poisons the mean: report n/a, not a fake number
        return None
    return statistics.mean(costs[m])

fr_cost = mean_cost(FRONTIER) if ncells.get(FRONTIER) else None
cost_agg = {"frontier": FRONTIER, "frontier_cost": fr_cost, "models": {}}
for m in sorted(MODELS_ORDER, key=lambda m: mean_cost(m) if mean_cost(m) is not None else 9e9):
    if not ncells.get(m): continue
    c = costs.get(m, [])
    mean_c = mean_cost(m)
    se_c = (statistics.stdev(c) / math.sqrt(len(c)) if len(c) > 1 else 0.0) if mean_c is not None else None
    saved = ((1 - mean_c / fr_cost) * 100
             if mean_c is not None and fr_cost is not None and fr_cost > 0 else None)
    cost_str = (f'${mean_c:.4f}+/-{se_c:.4f}/call' if mean_c is not None
                else '     n/a (unknown cost)     ')
    saved_str = f'{saved:5.1f}%' if saved is not None else '  n/a'
    print(f'  {m.split("/")[-1]:26s} n={ncells[m]:3d}  {cost_str}  '
          f'lat p90={p90(lats[m]):5.1f}s  rtok_med={int(statistics.median(rtoks[m]))//1:5d}  '
          f'empties={empties[m]}  err={errcells[m]}  saved={saved_str}')
    if unpriced[m]:
        print(f'      ^ {unpriced[m]}/{ncells[m]} cell(s) have UNKNOWN cost (no PRICING entry in tasks.py) - '
              f'$/call and saved are n/a for this model, NOT $0.0000/100% saved.')
    cost_agg["models"][m] = {"mean_cost": mean_c, "se_cost": se_c, "n": ncells[m],
                              "n_unpriced_cells": unpriced[m],
                              "p90_latency": nan_to_none(p90(lats[m])), "rtok_med": int(statistics.median(rtoks[m])),
                              "empties": empties[m], "err": errcells[m], "saved_pct": saved}
print(f"\nfrontier baseline: {FRONTIER}  "
      + (f"${fr_cost:.4f}/call (measured)" if fr_cost is not None
         else "$n/a/call - the frontier's own cost is unknown (no PRICING entry), so NO savings "
              "percentage can be computed for any candidate."))
print("Note: savings driven by one runaway cell are fragile - check the per-call stderr and run_sweep's top-cost cells.")
print("Next: judge.py for subjective quality, then fill templates/report_template.md.")

json.dump({"deterministic": det_agg, "cost": cost_agg}, open("outputs/grade_agg.json", "w"), indent=2)
print("saved -> outputs/grade_agg.json (structured stats for build_report.py)")
