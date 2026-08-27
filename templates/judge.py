"""judge.py - Step 5 (subjective tasks): blind, shuffled, multi-family panel with error bars.

For every subjective task x case x seed: collect all models' outputs (frontier included -
it is the quality anchor), anonymize to letters, RE-SHUFFLE per case, and score with each
judge at temperature 0 against the task's rubric. Judges MUST be from different model families
than every candidate (rigor.md #6). Aggregates per model across seeds x cases x judges:
mean +/- std +/- stderr + 95% CI, pairwise z vs the top scorer (ties reported as ties,
rigor.md #2), and a per-judge breakdown so no single judge silently drives the ranking.

Honesty details:
  - EMPTY outputs are scored 0 (an unusable output is a quality failure), never dropped -
    dropping them would give a flaky model survivor-biased credit (M4).
  - A judge whose JSON won't parse is retried, then LOGGED as a drop - never silently lost (M5).
  - Scores are clamped to 0-10; a per-model n imbalance is flagged before you trust the means.
  - The judges' "fabricated" flag is ADVISORY only - grade.py's deterministic check is the real
    fabrication metric (rigor.md #4).

Usage:  OR_KEY=<key> python3 judge.py
Needs:  tasks.py (JUDGES list + per-task "rubric" with {ctx}), outputs/seeds_raw.json
Output: outputs/judge_agg.json + console table
"""
import os, sys, json, time, random, statistics, urllib.request
from collections import defaultdict
from tasks import TASKS, JUDGES, FRONTIER

KEY = os.environ["OR_KEY"]
URL = "https://openrouter.ai/api/v1/chat/completions"
RAW = json.load(open("outputs/seeds_raw.json"))
SEEDS = sorted({r["seed"] for r in RAW})
SUBJECTIVE = [t for t in TASKS if t["kind"] == "subjective"]

fams = {j.split("/")[0] for j in JUDGES} & {r["model"].split("/")[0] for r in RAW}
if fams:
    print(f"ERROR: judge family overlaps candidate family: {fams} - same-family judges favor "
          f"their siblings' style (rigor.md #6 mandates a different family). Replace those "
          f"judges in tasks.py's JUDGES list before running judge.py again.")
    sys.exit(1)

def call(model, prompt):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 6000, "temperature": 0.0}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        r = json.load(resp)
    return r["choices"][0]["message"].get("content") or ""

def parse_json(text):                          # LAST parseable balanced object (reasoning precedes the answer)
    objs, depth, start = [], 0, None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0: objs.append(text[start:i + 1])
    for s in reversed(objs):
        try: return json.loads(s)
        except Exception: continue
    return None

random.seed(2026)                              # reproducible shuffles
scores = defaultdict(list)                     # model -> [overall...]
jscores = defaultdict(list)                    # (judge, model) -> [overall...]  (per-judge breakdown, M15)
fab = defaultdict(int); fabn = defaultdict(int)
drops = []                                     # (judge, task, case, seed) judge calls that never parsed
missing = []                                   # (judge, case, seed, model) candidates absent from a parsed reply

def judge_case(task, case, seed):
    recs = [r for r in RAW if r["task"] == task["task"] and r["case"] == case["id"]
            and r["seed"] == seed and not r.get("error")]
    present = {r["model"]: r.get("content", "").strip() for r in recs}
    if not present: return
    nonempty = {m: c for m, c in present.items() if c}
    empty = [m for m, c in present.items() if not c]
    if len(nonempty) > 26:
        raise SystemExit("judge.py supports <=26 candidates per case (A-Z labels); reduce MODELS.")
    models = list(nonempty); random.shuffle(models)             # fresh shuffle per case
    labels = {chr(65 + i): m for i, m in enumerate(models)}      # anonymize
    block = "\n\n".join(f"### CANDIDATE {L}\n{nonempty[m]}" for L, m in labels.items())
    prompt = (task["rubric"].format(ctx=case["input"]) + "\n\n" + block +
              '\n\nScore each candidate 0-10 OVERALL against the rubric. Set "fabricated": '
              'true if it invents specific facts/metrics not in the context. Output ONLY '
              'JSON: {"candidates":{"A":{"overall":N,"fabricated":bool},...}}')
    for j in JUDGES:
        pj = None
        for attempt in range(3):
            try: pj = parse_json(call(j, prompt))
            except Exception: pj = None
            if pj: break
            time.sleep(4)
        if not pj:                                              # judge failed for this case-seed: skip it for
            drops.append((j, task["task"], case["id"], seed))   # ALL models (stays balanced), but LOG it
            continue
        cand = pj.get("candidates", {})
        for L, m in labels.items():
            ov = cand.get(L, {}).get("overall")
            if not isinstance(ov, (int, float)):
                missing.append((j, case["id"], seed, m)); continue
            ov = max(0, min(10, ov))                            # clamp (a stray "85" can't poison the mean)
            scores[m].append(ov); jscores[(j, m)].append(ov); fabn[m] += 1
            if cand.get(L, {}).get("fabricated"): fab[m] += 1
        for m in empty:                                         # empty output = unusable = 0 from this judge (M4)
            scores[m].append(0); jscores[(j, m)].append(0); fabn[m] += 1
        time.sleep(1)

for seed in SEEDS:
    print(f"=== seed {seed} ===")
    for t in SUBJECTIVE:
        for c in t["cases"]:
            judge_case(t, c, seed)
    print(f"  running n per model: { {m.split('/')[-1]: len(v) for m, v in scores.items()} }")

if not scores:
    raise SystemExit(f"no subjective outputs scored: no subjective tasks, all outputs empty, or ALL "
                     f"{len(drops)} judge call(s) failed to parse (check JUDGES ids / max_tokens).")

# ---- aggregate: mean +/- std +/- stderr, 95% CI, z vs top, loss vs frontier ----
out = {}
print("\n" + "=" * 92)
print(f"BLIND PANEL  ({len(SEEDS)} seeds x cases x {len(JUDGES)} judges)  mean +/- std [95% CI]")
print("=" * 92)
ranked = sorted(scores, key=lambda m: -statistics.mean(scores[m]))
top = ranked[0]
fr_mean = statistics.mean(scores[FRONTIER]) if scores.get(FRONTIER) else None
ts = scores[top]; tse = statistics.pstdev(ts) / len(ts) ** 0.5
for m in ranked:
    s = scores[m]
    mean, sd = statistics.mean(s), statistics.pstdev(s)
    se = sd / len(s) ** 0.5
    z = (statistics.mean(ts) - mean) / max((tse ** 2 + se ** 2) ** 0.5, 1e-9)
    verdict = "not separable from top (tied != equivalent)" if z <= 1.96 else "CLEARLY WORSE"
    loss_pct = 100 * (fr_mean - mean) / fr_mean if fr_mean else None   # persisted below, not just printed
    loss = f"  loss_vs_frontier={loss_pct:5.1f}%" if loss_pct is not None else ""
    star = " <- frontier" if m == FRONTIER else ""
    print(f'  {m.split("/")[-1]:26s} {mean:5.2f} +/- {sd:4.2f} [{mean-1.96*se:.2f}-{mean+1.96*se:.2f}] '
          f'(n={len(s)})  z={z:4.2f} {verdict}{loss}{star}')
    out[m] = {"mean": round(mean, 2), "std": round(sd, 2), "stderr": round(se, 2), "n": len(s),
              "z_vs_top": round(z, 2), "verdict": verdict, "is_frontier": m == FRONTIER,
              "loss_vs_frontier_pct": round(loss_pct, 1) if loss_pct is not None else None,
              "judge_fab_flags_advisory": f"{fab[m]}/{fabn[m]}"}

# ---- per-judge breakdown: does any single judge drive the ranking? (rigor.md #6, M15) ----
print("\nPER-JUDGE mean per model (watch for one judge disagreeing with the panel):")
hdr = "  " + " " * 26 + "".join(f'{j.split("/")[-1][:14]:>15s}' for j in JUDGES)
print(hdr)
for m in ranked:
    row = "".join(f'{statistics.mean(jscores[(j,m)]):>15.2f}' if jscores.get((j, m)) else f'{"-":>15s}' for j in JUDGES)
    print(f'  {m.split("/")[-1]:26s}{row}')

# ---- integrity flags ----
ns = [len(v) for v in scores.values()]
if ns and max(ns) - min(ns) > 0.2 * max(ns):
    print(f"\nWARNING: unbalanced judge n across models ({min(ns)}-{max(ns)}) - some judge x case scores dropped; "
          f"the means are over different case mixes. Investigate before trusting the ranking.")
if drops:
    print(f"WARNING: {len(drops)} judge call(s) never parsed and were dropped (logged): {drops[:5]}{'...' if len(drops)>5 else ''}")
if missing:
    print(f"NOTE: {len(missing)} candidate score(s) absent from an otherwise-parsed reply (logged).")

json.dump({"agg": out, "drops": len(drops), "missing": len(missing)}, open("outputs/judge_agg.json", "w"), indent=2)
print('\nadvisory judge fabrication flags (trust grade.py\'s deterministic check instead):')
print("  " + "  ".join(f'{m.split("/")[-1]}={fab[m]}/{fabn[m]}' for m in ranked))
print("saved -> outputs/judge_agg.json")
