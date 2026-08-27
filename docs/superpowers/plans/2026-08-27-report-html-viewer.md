# Report HTML Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist grade.py's per-case detail (pass/parsed/hard/trap) to `outputs/grade_agg.json`, then render it — alongside the existing aggregate tradeoff table — as a single self-contained `outputs/report.html` via a new `build_report_html.py` script.

**Architecture:** `grade.py`'s per-case loop already computes everything needed for per-case detail; it currently only prints it. Add one `cases` array to the JSON it already writes. `build_report_html.py` is a new sibling to `build_report.py`, same zero-config, no-network, reads-the-aggregate-JSON-only convention, emitting HTML instead of Markdown with one extra section (the per-case grid) that `build_report.py` cannot show.

**Tech Stack:** Python 3 stdlib only (`json`, `os`, `html`, `datetime`) — no new dependency, matching every other template script.

**Spec:** `docs/superpowers/specs/2026-08-27-report-html-viewer-design.md`

## Global Constraints

- No CDN, no JS framework, no build step, no server — `outputs/report.html` must open correctly via `file://` with network fully disabled.
- `build_report_html.py` never reads `outputs/seeds_raw.json`. It reads only `outputs/grade_agg.json`, `outputs/judge_agg.json`, and `tasks.py` — same inputs as `build_report.py`.
- Only aggregated counts, means, and small category-label dispersions are ever inlined into the HTML — never a raw model completion string. This is the same aggregation level `grade.py`'s console output already reports.
- `parsed` and `ok` are kept as distinct fields everywhere they both apply — never collapsed into one pass/fail number.
- Recommendation-per-task and Deployment-notes sections stay visibly unfilled (`<FILL IN...>` text, wrapped so it's visually obvious, e.g. `<mark>`) — this script must never auto-decide the business call, exactly like `build_report.py`.
- v1 scope only: aggregate table(s), statistical reading, per-case grid, `<FILL IN>` sections, limitations paragraph. No filtering/sorting UI, no charting library, no cross-run history/diffing.
- Every new template script (`build_report_html.py`) gets its own `selftest.py` coverage in the same task that creates it — offline, fixture-driven, no network, matching the existing suite's pattern (`workdir()` / `write()` / `run()` / `check()` helpers already defined in `templates/selftest.py`).

---

### Task 1: `grade.py` — persist per-case detail to `outputs/grade_agg.json`

**Files:**
- Modify: `templates/grade.py` (multiple locations — see steps below)
- Modify: `templates/selftest.py` (new fixture block)

**Interfaces:**
- Consumes: nothing new — reuses `grade.py`'s existing per-case loop variables (`t`, `case`, `split`, `m`, `recs`, and the kind-specific locals already computed in each branch).
- Produces: `outputs/grade_agg.json["cases"]` — a list of dicts, one per `(task, case, model)`, with this exact key set (every key present on every row; unused keys are `None`):
  `task, case_id, kind, model, split, hard, trap, note, n, parsed, ok, mae, tolerance, fabricated, banned, len_violations, boundary_spread`.
  Population per kind:
  - `structured` (non-boundary): `parsed`, `ok` set; rest `None`.
  - `structured` (boundary, `ref is None`): `parsed`, `boundary_spread` set; `ok` stays `None`.
  - `numeric`: `parsed`, `ok`, `mae`, `tolerance` set; rest `None`.
  - `subjective`: `parsed` (always `= n`), `fabricated`, `banned`, `len_violations` set; rest `None`.
  This is what Task 2's `build_report_html.py` reads as `GRADE["cases"]`.

- [ ] **Step 1: Add the new selftest fixture (it will fail — `grade.py` doesn't produce `cases` yet)**

Open `templates/selftest.py`. Immediately before the final block (`# ---------------------------------------------------------------------------\nprint()\nif FAILURES:`), insert this new block:

```python
# ---------------------------------------------------------------------------
print("\ngrade.py: grade_agg.json's cases array must carry per-(task,case,model) detail for every kind")
d = workdir("grade.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "test-vendor/cheap-model", "provider": "openrouter"}]
SEEDS = [11, 23]
TASKS = [
    {
        "task": "extract", "kind": "structured", "system": "extract",
        "max_tokens": 100, "temperature": 0.0,
        "cases": [
            {"id": "normal", "input": "x", "ref": {"tier": "Hot"}, "validated": True,
             "ref_fields": ["tier"], "hard": True},
            {"id": "boundary", "input": "x", "ref": None},
        ],
    },
    {
        "task": "score", "kind": "numeric", "system": "score it",
        "max_tokens": 50, "temperature": 0.0,
        "cases": [{"id": "num1", "input": "x", "ref": 10, "validated": True, "tolerance": 2}],
    },
    {
        "task": "summarize", "kind": "subjective", "system": "summarize",
        "max_tokens": 100, "temperature": 0.7,
        "cases": [{"id": "sub1", "input": "x", "ref": None, "word_range": [1, 100]}],
    },
]
''')
seeds_raw = []
for m in ("test-vendor/frontier-model", "test-vendor/cheap-model"):
    for s in (11, 23):
        seeds_raw.append({"task": "extract", "case": "normal", "model": m, "seed": s,
                           "content": json.dumps({"tier": "Hot"}), "cost": 0.001})
        seeds_raw.append({"task": "extract", "case": "boundary", "model": m, "seed": s,
                           "content": json.dumps({"tier": "Cold"}), "cost": 0.001})
        seeds_raw.append({"task": "score", "case": "num1", "model": m, "seed": s,
                           "content": "the score is 9", "cost": 0.001})
        seeds_raw.append({"task": "summarize", "case": "sub1", "model": m, "seed": s,
                           "content": "a short summary", "cost": 0.001})
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps(seeds_raw))

p = run(d, "grade.py")
check("grade.py exits 0", p.returncode == 0, p.stderr[-2000:])
agg = json.load(open(os.path.join(d, "outputs", "grade_agg.json")))
cases = agg.get("cases", [])
check("grade_agg.json has a cases array", isinstance(cases, list) and len(cases) > 0, cases)
check("cases array has one row per (task,case,model): 4 case-defs x 2 models = 8", len(cases) == 8, cases)

by_case = {(r["case_id"], r["model"]): r for r in cases}
normal = by_case.get(("normal", "test-vendor/frontier-model"))
check("structured non-boundary row has parsed and ok set, boundary_spread null",
      normal and normal["parsed"] == 2 and normal["ok"] == 2 and normal["boundary_spread"] is None, normal)
check("structured non-boundary row carries hard=True from tasks.py", normal and normal["hard"] is True, normal)

boundary = by_case.get(("boundary", "test-vendor/frontier-model"))
check("structured boundary row has boundary_spread set, ok null",
      boundary and boundary["boundary_spread"] and boundary["ok"] is None, boundary)

num = by_case.get(("num1", "test-vendor/frontier-model"))
check("numeric row has mae and tolerance set, ok set", num and num["mae"] is not None and num["tolerance"] == 2
      and num["ok"] is not None, num)

sub = by_case.get(("sub1", "test-vendor/frontier-model"))
check("subjective row has fabricated/banned/len_violations set, mae null",
      sub and sub["fabricated"] is not None and sub["banned"] is not None
      and sub["len_violations"] is not None and sub["mae"] is None, sub)
shutil.rmtree(d, ignore_errors=True)

```

- [ ] **Step 2: Run selftest.py to confirm the new checks fail**

Run: `python3 templates/selftest.py`
Expected: FAIL — the new checks report `[FAIL] grade_agg.json has a cases array` (and the ones after it), because `grade.py` does not write a `"cases"` key yet. Every other existing check still passes.

- [ ] **Step 3: Implement the `cases` array in `grade.py`**

Open `templates/grade.py`.

First, add a docstring note about the new array's data-safety scope. Find this line in the module
docstring's "Honesty" bullet list:

```python
  - A cell with an UNKNOWN cost (cost=None: e.g. a provider whose model has no tasks.py PRICING
    entry) is never counted as $0. Any such cell makes that model's $/call and savings read "n/a"
    (null in grade_agg.json). A fabricated "$0.0000/call, 100% saved" is the exact business number
    this tool exists to get right.
```

Add one more bullet immediately after it (still inside the docstring, before the closing `"""`):

```python
  - grade_agg.json's "cases" array (one row per task/case/model) carries only counts, means, and
    small category-label dispersions - the same aggregation level this script's console output
    already prints. It never carries a raw model completion string.
```

Next, relocate `nan_to_none` so it's defined before the per-case loop (it will be called from inside that loop). Delete this existing block (currently right after the loop, before `det_agg = None`):

```python
def nan_to_none(x):                            # NaN isn't valid JSON; null is the honest "n/a"
    return None if isinstance(x, float) and x != x else x

det_agg = None
```

Replace it with just:

```python
det_agg = None
```

Now find this line (the start of the per-case processing section):

```python
case_fracs = defaultdict(list)                 # model -> [pass fraction per case]  (case-level unit, M2)
```

Replace it with:

```python
def nan_to_none(x):                            # NaN isn't valid JSON; null is the honest "n/a" (moved up: base_row needs it)
    return None if isinstance(x, float) and x != x else x

def base_row(t, case, split, m, n):            # one outputs/grade_agg.json["cases"] row per (task,case,model)
    return {"task": t["task"], "case_id": case["id"], "kind": t["kind"], "model": m,
            "split": split, "hard": bool(case.get("hard")), "trap": bool(case.get("trap")),
            "note": case.get("note"), "n": n, "parsed": None, "ok": None, "mae": None,
            "tolerance": None, "fabricated": None, "banned": None, "len_violations": None,
            "boundary_spread": None}

case_rows = []                                 # persisted as grade_agg.json["cases"] - per-case pass/parsed/hard/trap detail
case_fracs = defaultdict(list)                 # model -> [pass fraction per case]  (case-level unit, M2)
```

Next, find the boundary-case branch:

```python
                if ref is None:                # boundary case: report spread, don't pass/fail
                    fields = case.get("ref_fields", []) + list(case.get("tolerance", {}))
                    if not fields:             # none declared: every top-level scalar the models emitted
                        fields = sorted({k for v in vals if v for k, x in v.items()
                                         if isinstance(x, (str, int, float, bool))})
                    spread = {f: dict(Counter(str(get_path(v, f)) for v in vals if v)) for f in fields}
                    print(f'  {name:26s} boundary spread (parsed {parsed}/{len(recs)}): {spread or "(no JSON parsed)"}')
                    continue
```

Replace with:

```python
                if ref is None:                # boundary case: report spread, don't pass/fail
                    fields = case.get("ref_fields", []) + list(case.get("tolerance", {}))
                    if not fields:             # none declared: every top-level scalar the models emitted
                        fields = sorted({k for v in vals if v for k, x in v.items()
                                         if isinstance(x, (str, int, float, bool))})
                    spread = {f: dict(Counter(str(get_path(v, f)) for v in vals if v)) for f in fields}
                    print(f'  {name:26s} boundary spread (parsed {parsed}/{len(recs)}): {spread or "(no JSON parsed)"}')
                    row = base_row(t, case, split, m, len(recs))
                    row["parsed"] = parsed
                    row["boundary_spread"] = spread
                    case_rows.append(row)
                    continue
```

Next, find the structured non-boundary tail:

```python
                    ok += good
                case_fracs[m].append(ok / len(recs))
                print(f'  {name:26s} pass {ok}/{len(recs)}  (parsed {parsed}/{len(recs)})')
```

Replace with:

```python
                    ok += good
                case_fracs[m].append(ok / len(recs))
                print(f'  {name:26s} pass {ok}/{len(recs)}  (parsed {parsed}/{len(recs)})')
                row = base_row(t, case, split, m, len(recs))
                row["parsed"] = parsed
                row["ok"] = ok
                case_rows.append(row)
```

Next, find the numeric branch:

```python
                mae = statistics.mean(errs) if errs else float("nan")
                print(f'  {name:26s} MAE={mae:.2f}  within +/-{tol}: {ok}/{len(recs)}  unparsed={len(recs)-len(errs)}')
```

Replace with:

```python
                mae = statistics.mean(errs) if errs else float("nan")
                print(f'  {name:26s} MAE={mae:.2f}  within +/-{tol}: {ok}/{len(recs)}  unparsed={len(recs)-len(errs)}')
                row = base_row(t, case, split, m, len(recs))
                row["parsed"] = len(errs)
                row["ok"] = ok
                row["mae"] = nan_to_none(mae)
                row["tolerance"] = tol
                case_rows.append(row)
```

Next, find the subjective branch tail:

```python
                print(f'  {name:26s} fabricated_metric={nf}/{len(recs)}{" e.g." + ex if ex else ""}  '
                      f'banned={nb}/{len(recs)}  len_violations={nw}/{len(recs)}')
```

Replace with:

```python
                print(f'  {name:26s} fabricated_metric={nf}/{len(recs)}{" e.g." + ex if ex else ""}  '
                      f'banned={nb}/{len(recs)}  len_violations={nw}/{len(recs)}')
                row = base_row(t, case, split, m, len(recs))
                row["parsed"] = len(recs)
                row["fabricated"] = nf
                row["banned"] = nb
                row["len_violations"] = nw
                case_rows.append(row)
```

Finally, find the closing `json.dump`:

```python
json.dump({"deterministic": det_agg, "cost": cost_agg}, open("outputs/grade_agg.json", "w"), indent=2)
```

Replace with:

```python
json.dump({"deterministic": det_agg, "cost": cost_agg, "cases": case_rows}, open("outputs/grade_agg.json", "w"), indent=2)
```

- [ ] **Step 4: Run selftest.py to confirm all checks pass**

Run: `python3 templates/selftest.py`
Expected: `all selftest checks passed`, exit 0. Every prior check (including the cost-honesty and content_json ones) must still pass unchanged — this task only adds data, it does not alter `det_agg`/`cost_agg` computation.

- [ ] **Step 5: Commit**

```bash
git add templates/grade.py templates/selftest.py
git commit -m "grade.py: persist per-case pass/parsed/hard/trap detail to grade_agg.json"
```

---

### Task 2: `templates/build_report_html.py` — self-contained HTML report

**Files:**
- Create: `templates/build_report_html.py`
- Modify: `templates/selftest.py` (new fixture block)

**Interfaces:**
- Consumes: `outputs/grade_agg.json["deterministic"]`, `["cost"]` (existing shapes, unchanged — same as `build_report.py` already reads), `outputs/grade_agg.json["cases"]` (Task 1's new shape — exact keys listed in Task 1), `outputs/judge_agg.json["agg"]` (existing shape, unchanged), `tasks.py`'s `TASKS`/`MODELS`/`SEEDS`/`FRONTIER`.
- Produces: `outputs/report.html`. No other task reads this file.

- [ ] **Step 1: Add the new selftest fixture (it will fail — the script doesn't exist yet)**

Open `templates/selftest.py`. Immediately before the final block (same insertion point as Task 1's addition — after it, so this new block comes right after Task 1's), insert:

```python
# ---------------------------------------------------------------------------
print("\nbuild_report_html.py: must assemble a self-contained HTML report with per-case detail")
d = workdir("build_report_html.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "test-vendor/cheap-model", "provider": "openrouter"}]
SEEDS = [11, 23, 42]
TASKS = [{
    "task": "extract", "kind": "structured", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [
        {"id": "normal", "input": "x", "hard": True},
        {"id": "boundary", "input": "x"},
    ],
}]
''')
write(os.path.join(d, "outputs", "grade_agg.json"), json.dumps({
    "deterministic": {"n_cases": 1, "mdd_pp": 15.0, "models": {
        "test-vendor/frontier-model": {"pass_rate": 1.0, "se": None, "n_cases": 1, "z_vs_best": 0.0,
                   "verdict": "not separable from best (tied != equivalent)", "is_frontier": True},
        "test-vendor/cheap-model": {"pass_rate": 0.5, "se": None, "n_cases": 1, "z_vs_best": 5.0,
                                     "verdict": "CLEARLY WORSE", "is_frontier": False},
    }},
    "cost": {"frontier": "test-vendor/frontier-model", "frontier_cost": 0.01, "models": {
        "test-vendor/frontier-model": {"mean_cost": 0.01, "se_cost": 0.0, "n": 3, "p90_latency": 1.0,
                   "rtok_med": 0, "empties": 0, "err": 0, "saved_pct": 0.0},
        "test-vendor/cheap-model": {"mean_cost": 0.002, "se_cost": 0.0, "n": 3, "p90_latency": 0.5,
                                     "rtok_med": 0, "empties": 0, "err": 0, "saved_pct": 80.0},
    }},
    "cases": [
        {"task": "extract", "case_id": "normal", "kind": "structured", "model": "test-vendor/frontier-model",
         "split": "val", "hard": True, "trap": False, "note": "designed answer: x",
         "n": 3, "parsed": 3, "ok": 3, "mae": None, "tolerance": None, "fabricated": None,
         "banned": None, "len_violations": None, "boundary_spread": None},
        {"task": "extract", "case_id": "normal", "kind": "structured", "model": "test-vendor/cheap-model",
         "split": "val", "hard": True, "trap": False, "note": "designed answer: x",
         "n": 3, "parsed": 1, "ok": 0, "mae": None, "tolerance": None, "fabricated": None,
         "banned": None, "len_violations": None, "boundary_spread": None},
        {"task": "extract", "case_id": "boundary", "kind": "structured", "model": "test-vendor/frontier-model",
         "split": "val", "hard": False, "trap": False, "note": None,
         "n": 3, "parsed": 3, "ok": None, "mae": None, "tolerance": None, "fabricated": None,
         "banned": None, "len_violations": None, "boundary_spread": {"tier": {"Hot": 3}}},
        {"task": "extract", "case_id": "boundary", "kind": "structured", "model": "test-vendor/cheap-model",
         "split": "val", "hard": False, "trap": False, "note": None,
         "n": 3, "parsed": 3, "ok": None, "mae": None, "tolerance": None, "fabricated": None,
         "banned": None, "len_violations": None, "boundary_spread": {"tier": {"Cold": 2, "Hot": 1}}},
    ],
}))
p = run(d, "build_report_html.py")
check("build_report_html.py exits 0", p.returncode == 0, p.stderr[-2000:])
report_path = os.path.join(d, "outputs", "report.html")
check("writes outputs/report.html", os.path.exists(report_path))
report = open(report_path).read() if os.path.exists(report_path) else ""
check("output is non-empty HTML", report.startswith("<!doctype html>") and "</html>" in report, report[:200])
check("aggregate table shows the frontier's real pass-rate", "100.0%" in report, report)
check("aggregate table shows the cheap model's real pass-rate", "50.0%" in report, report)
check("per-case grid has one row per case", "extract/normal" in report and "extract/boundary" in report, report)
check("per-case grid has a column for each model", "frontier-model" in report and "cheap-model" in report, report)
check("a parsed<n cell is visually distinguishable from an ok<n cell (parsefail marker present)",
      "parsefail" in report and "(parsed 1/3)" in report, report)
check("boundary case renders its spread dict, not a pass fraction",
      "spread" in report and "Hot=3" in report, report)
check("HARD tag is rendered for the hard case", "HARD" in report, report)
check("Recommendation/Deployment sections stay visibly unfilled (FILL IN present, wrapped in <mark>)",
      report.count("<mark>") >= 6 and "FILL IN" in report, report)
shutil.rmtree(d, ignore_errors=True)

```

- [ ] **Step 2: Run selftest.py to confirm the new checks fail**

Run: `python3 templates/selftest.py`
Expected: FAIL — `subprocess` reports a non-zero return code (or the file-not-found equivalent) because `build_report_html.py` does not exist yet; every check in this new block fails or errors.

- [ ] **Step 3: Implement `templates/build_report_html.py`**

Create `templates/build_report_html.py` with this exact content:

```python
"""build_report_html.py - Step 6b: a single self-contained outputs/report.html visualizing
grade.py/judge.py's real numbers, including the per-case detail (parsed/ok/hard/trap) that
outputs/report.md's tradeoff table alone cannot show.

Reads the same JSON grade.py/judge.py already produce (outputs/grade_agg.json,
outputs/judge_agg.json) plus tasks.py - never outputs/seeds_raw.json, which holds raw model
completions. Only aggregated counts, means, and small category-label dispersions are inlined
into the page - the same aggregation level grade.py's console output already prints, never a raw
completion string - so outputs/report.html carries the same "never commit, may hold
per-engagement data" status as everything else under outputs/ (see .gitignore).

It deliberately does NOT decide anything, same as build_report.py: Verdict, Recommendation-per-
task, and Deployment-notes stay visibly unfilled - a report that auto-fills the business call is
a report that skipped the one step this whole methodology exists to protect.

Usage:  python3 build_report_html.py
Needs:  tasks.py, outputs/grade_agg.json (from grade.py) and/or outputs/judge_agg.json (from judge.py)
Output: outputs/report.html + a console echo of the path
"""
import html as htmllib
import json, os, sys
from datetime import date
from tasks import TASKS, MODELS, SEEDS, FRONTIER

GRADE = json.load(open("outputs/grade_agg.json")) if os.path.exists("outputs/grade_agg.json") else None
JUDGE = json.load(open("outputs/judge_agg.json")) if os.path.exists("outputs/judge_agg.json") else None
if GRADE is None and JUDGE is None:
    sys.exit("no outputs/grade_agg.json or outputs/judge_agg.json found - run grade.py "
             "(and judge.py, for subjective tasks) first.")

FILL = "<FILL IN - business call, see table above>"


def esc(s): return htmllib.escape(str(s))
def short(m): return m.split("/")[-1]
def fmt_pct(x, dp=1): return "n/a" if x is None else f"{x:.{dp}f}%"
def fmt_money(x): return "n/a" if x is None else f"${x:.4f}"
def fmt_num(x, dp=2): return "n/a" if x is None else f"{x:.{dp}f}"


n_cases = sum(len(t["cases"]) for t in TASKS)
n_models = len(MODELS)
n_seeds = len(SEEDS)
n_cells = n_models * n_cases * n_seeds

cost_by_model = (GRADE or {}).get("cost", {}).get("models", {}) or {}
unpriced_models = [m for m, v in cost_by_model.items() if v.get("mean_cost") is None]
measured_total = (sum(v["mean_cost"] * v["n"] for v in cost_by_model.values())
                  if cost_by_model and not unpriced_models else None)

sections = []
def emit(s): sections.append(s)


def worse_and_tied(models_by_verdict_key):
    tied = [m for m, v in models_by_verdict_key.items() if "CLEARLY WORSE" not in v["verdict"]]
    worse = [m for m, v in models_by_verdict_key.items() if "CLEARLY WORSE" in v["verdict"]]
    return tied, worse


emit(f"<h1>Migration decision report - {esc(', '.join(t['task'] for t in TASKS))}</h1>")
emit("<p class='meta'>"
     f"<b>Date:</b> {esc(date.today().isoformat())} &mdash; <b>Incumbent:</b> {esc(FRONTIER)} &mdash; "
     f"<b>Task volume:</b> <mark>{esc(FILL)}</mark> (calls/month, $/month)</p>")
emit("<p class='meta'>"
     f"<b>Harness:</b> {n_models} models x {n_cases} cases x {n_seeds} seeds = {n_cells} cells, "
     f"provider-pinned &mdash; measured sweep cost: {esc(fmt_money(measured_total))}"
     + (f" (cost UNKNOWN for {esc(', '.join(short(m) for m in unpriced_models))} - no PRICING entry "
        f"in tasks.py; this is not $0)" if unpriced_models else "")
     + (" (excludes judge panel API cost, not tracked separately)" if JUDGE else "")
     + "</p>")
emit("<p class='meta'><b>Golden refs validated by:</b> "
     f"<mark>{esc(FILL)}</mark> (grade.py already refuses to run on an unvalidated ref, but WHO "
     "signed off is not machine-checkable)</p>")
emit("<h2>Verdict (one paragraph, up front)</h2>")
emit(f"<p class='fill'><mark>{esc(FILL)}</mark> - swap / swap-with-guard / optimize-first / keep "
     "frontier, per task, with the projected annual savings at stated volume. If the top is a "
     "statistical tie, say so.</p>")

if GRADE and GRADE.get("deterministic"):
    det = GRADE["deterministic"]
    emit("<h2>The tradeoff table - deterministic tasks (case-clustered pass-rate)</h2>")
    rows = []
    fr_rate = det["models"].get(FRONTIER, {}).get("pass_rate")
    for m, v in sorted(det["models"].items(), key=lambda kv: -kv[1]["pass_rate"]):
        ci = f"{v['se']*196:.1f}pp" if v.get("se") is not None else "n/a"
        loss = None
        if not v["is_frontier"] and fr_rate:
            loss = (fr_rate - v["pass_rate"]) / fr_rate * 100
        c = cost_by_model.get(m, {})
        rows.append("<tr>"
            f"<td>{esc(short(m))}{' (baseline)' if v['is_frontier'] else ''}</td>"
            f"<td>{v['pass_rate']*100:.1f}% &plusmn; {esc(ci)}</td>"
            f"<td>{'-' if v['is_frontier'] else esc(fmt_pct(loss))}</td>"
            f"<td>{esc(fmt_money(c.get('mean_cost')))} &plusmn; {esc(fmt_money(c.get('se_cost')))}</td>"
            f"<td>{'-' if v['is_frontier'] else esc(fmt_pct(c.get('saved_pct')))}</td>"
            f"<td>{esc(fmt_num(c.get('p90_latency'), 1))}s</td>"
            f"<td>{esc(v['verdict'])}</td></tr>")
    emit("<table><thead><tr><th>Candidate</th><th>Pass-rate (mean &plusmn; 95% CI)</th>"
         "<th>Accuracy loss vs frontier</th><th>$/call (mean &plusmn; stderr)</th><th>$ saved</th>"
         "<th>Latency p90</th><th>Verdict</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    emit(f"<p class='note'>Quality source: deterministic pass-rate, CASE-clustered, n_cases={det['n_cases']}. "
         + (f"Minimum difference detectable at this n: ~{det['mdd_pp']:.0f}pp - smaller gaps are "
            "undetectable here." if det.get("mdd_pp") is not None else "") + "</p>")
    tied, worse = worse_and_tied(det["models"])
    emit("<h3>Statistical reading (deterministic)</h3>")
    emit(f"<p><b>Top cluster (statistically tied):</b> {esc(', '.join(short(m) for m in tied) or 'none')}</p>")
    emit(f"<p><b>Clearly worse:</b> {esc(', '.join(short(m) for m in worse) or 'none')}</p>")

if JUDGE and JUDGE.get("agg"):
    agg = JUDGE["agg"]
    emit("<h2>The tradeoff table - subjective tasks (blind multi-family panel)</h2>")
    rows = []
    for m, v in sorted(agg.items(), key=lambda kv: -kv[1]["mean"]):
        ci_lo, ci_hi = v["mean"] - 1.96 * v["stderr"], v["mean"] + 1.96 * v["stderr"]
        c = cost_by_model.get(m, {})
        loss = v.get("loss_vs_frontier_pct")
        rows.append("<tr>"
            f"<td>{esc(short(m))}{' (baseline)' if v.get('is_frontier') else ''}</td>"
            f"<td>{v['mean']:.2f} &plusmn; {v['std']:.2f} [{ci_lo:.2f}-{ci_hi:.2f}]</td>"
            f"<td>{'-' if v.get('is_frontier') else esc(fmt_pct(loss))}</td>"
            f"<td>{esc(fmt_money(c.get('mean_cost')))} &plusmn; {esc(fmt_money(c.get('se_cost')))}</td>"
            f"<td>{'-' if v.get('is_frontier') else esc(fmt_pct(c.get('saved_pct')))}</td>"
            f"<td>{esc(fmt_num(c.get('p90_latency'), 1))}s</td>"
            f"<td>{esc(v.get('verdict', 'n/a'))}</td></tr>")
    emit("<table><thead><tr><th>Candidate</th><th>Quality (mean &plusmn; std [95% CI])</th>"
         "<th>Accuracy loss vs frontier</th><th>$/call (mean &plusmn; stderr)</th><th>$ saved</th>"
         "<th>Latency p90</th><th>Verdict</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    emit(f"<p class='note'>Quality source: blind panel, n={next(iter(agg.values()))['n']} scores/model "
         f"({n_seeds} seeds x cases x judges). Judge fabrication flags are ADVISORY - trust "
         "grade.py's deterministic fabrication check over these.</p>")
    if JUDGE.get("drops"):
        emit(f"<p class='note'>NOTE: {JUDGE['drops']} judge call(s) never parsed and were dropped.</p>")
    if JUDGE.get("missing"):
        emit(f"<p class='note'>NOTE: {JUDGE['missing']} candidate score(s) were absent from an "
             "otherwise-parsed judge reply.</p>")
    tied, worse = worse_and_tied(agg)
    emit("<h3>Statistical reading (subjective panel)</h3>")
    emit(f"<p><b>Top cluster (statistically tied):</b> {esc(', '.join(short(m) for m in tied) or 'none')}</p>")
    emit(f"<p><b>Clearly worse:</b> {esc(', '.join(short(m) for m in worse) or 'none')}</p>")

if GRADE and GRADE.get("cases"):
    cases = GRADE["cases"]
    emit("<h2>Per-case detail</h2>")
    emit("<p class='note'>Pass counts alone can hide a total parse failure. A cell tagged "
         "<span class='parsefail'>parsed X/n</span> means the model did not even produce usable "
         "output on that many calls - a different, more severe failure than answering wrong.</p>")
    models_order = sorted({r["model"] for r in cases})
    by_case = {}
    for r in cases:
        key = (r["task"], r["case_id"])
        entry = by_case.setdefault(key, {"hard": r["hard"], "trap": r["trap"], "note": r.get("note"), "rows": {}})
        entry["rows"][r["model"]] = r
    emit("<table class='grid'><thead><tr><th>Case</th>"
         + "".join(f"<th>{esc(short(m))}</th>" for m in models_order) + "</tr></thead><tbody>")
    for (task, case_id), info in by_case.items():
        tag = (" <span class='hard'>HARD</span>" if info["hard"]
               else (" <span class='trap'>TRAP</span>" if info["trap"] else ""))
        emit(f"<tr><td>{esc(task)}/{esc(case_id)}{tag}"
             + (f"<div class='casenote'>{esc(info['note'])}</div>" if info.get("note") else "") + "</td>")
        for m in models_order:
            r = info["rows"].get(m)
            if r is None:
                emit("<td>-</td>")
            elif r.get("boundary_spread") is not None:
                parts = []
                for field, counts in r["boundary_spread"].items():
                    counts_txt = ", ".join(f"{esc(val)}={cnt}" for val, cnt in counts.items())
                    parts.append(f"{esc(field)}: {counts_txt}")
                emit(f"<td class='boundary'>spread &mdash; {'; '.join(parts)}</td>")
            elif r.get("mae") is not None:
                emit(f"<td>MAE={r['mae']:.2f} ok {r['ok']}/{r['n']}</td>")
            elif r.get("fabricated") is not None:
                emit(f"<td>fab={r['fabricated']} banned={r['banned']} len_viol={r['len_violations']}</td>")
            else:
                ok, n, parsed = r.get("ok"), r.get("n"), r.get("parsed")
                cls = "parsefail" if parsed is not None and parsed < n else ("fail" if ok is not None and ok < n else "pass")
                extra = (f" <span class='parsefail'>(parsed {parsed}/{n})</span>"
                         if parsed is not None and parsed < n else "")
                emit(f"<td class='{cls}'>{ok}/{n}{extra}</td>")
        emit("</tr>")
    emit("</tbody></table>")

emit("<h2>Recommendation per task</h2>")
emit("<table><thead><tr><th>Task</th><th>Recommendation</th>"
     "<th>Model (+ provider pin, quant)</th><th>Guard required</th>"
     "<th>Projected annual savings</th></tr></thead><tbody>")
for t in TASKS:
    emit(f"<tr><td>{esc(t['task'])}</td>" + "".join(f"<td><mark>{esc(FILL)}</mark></td>" for _ in range(4)) + "</tr>")
emit("</tbody></table>")

emit("<h2>Deployment notes</h2>")
emit(f"<p class='fill'><mark>{esc(FILL)}</mark> - orchestration pattern, guards wired into "
     "production, rollback plan, re-run triggers.</p>")

emit("<h2>Limitations (mandatory - do not trim)</h2>")
n_judge_scores = next(iter(JUDGE["agg"].values()))["n"] if JUDGE and JUDGE.get("agg") else None
emit(f"<p>This verdict is based on {n_cases} case(s) x {n_seeds} seed(s) per model"
     + (f" ({n_judge_scores} panel scores/model)" if n_judge_scores else "")
     + f", on THESE prompts, THESE pinned providers, as of {esc(date.today().isoformat())}. "
     "It is not a general model ranking. Prices and the model field turn over monthly - "
     "treat this report as perishable; re-run on prompt change, pin/quant change, a notable "
     "new model, or quarterly.</p>")

CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
h1, h2, h3 { border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }
th { background: #f4f4f4; }
mark { background: #ffe28a; padding: 0 0.2rem; }
.note { color: #555; font-size: 0.85rem; }
.parsefail { color: #b30000; font-weight: bold; }
.fail { background: #ffecec; }
.pass { background: #ecffec; }
.boundary { background: #f0f0ff; }
.hard { background: #ffd8a8; padding: 0 0.3rem; border-radius: 3px; font-size: 0.75rem; }
.trap { background: #ffb3b3; padding: 0 0.3rem; border-radius: 3px; font-size: 0.75rem; }
.casenote { color: #777; font-size: 0.8rem; }
"""

html_doc = ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Migration decision report</title><style>{CSS}</style></head><body>"
            + "".join(sections) + "</body></html>")

os.makedirs("outputs", exist_ok=True)
open("outputs/report.html", "w").write(html_doc)
print(f"saved -> outputs/report.html ({len(html_doc)} bytes)")
```

- [ ] **Step 4: Run selftest.py to confirm all checks pass**

Run: `python3 templates/selftest.py`
Expected: `all selftest checks passed`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add templates/build_report_html.py templates/selftest.py
git commit -m "add build_report_html.py: self-contained HTML report with per-case detail"
```

---

### Task 3: doc fixes — point at the new persisted detail

**Files:**
- Modify: `templates/build_report.py:144-148`
- Modify: `README.md:46-49`

**Interfaces:**
- Consumes: nothing (text-only change).
- Produces: nothing new (no other task depends on this task's output).

- [ ] **Step 1: Fix `build_report.py`'s now-inaccurate comment**

Open `templates/build_report.py`. Find:

```python
emit("## Deterministic findings (these carry more weight than the panel)")
emit()
emit("Fabrication rates, trap/boundary-case results, and format/guard-violation counts are printed "
     "per-case by grade.py's console output but are not persisted to grade_agg.json (they are "
     "case-level detail, not a clean aggregate) - copy the relevant lines from that run here.")
emit()
```

Replace with:

```python
emit("## Deterministic findings (these carry more weight than the panel)")
emit()
emit("Fabrication rates, trap/boundary-case results, and parse-failure counts are persisted "
     "per-(task,case,model) in outputs/grade_agg.json's \"cases\" array (grade.py) - run "
     "build_report_html.py for a rendered per-case grid, or copy the relevant lines from "
     "grade.py's console output here.")
emit()
```

- [ ] **Step 2: Mention the HTML viewer in `README.md`**

Open `README.md`. Find:

```markdown
Then `build_golden.py` -> (human validation) -> `run_sweep.py` -> `grade.py` -> `build_report.py`,
the run order in `SKILL.md`. API keys are demanded only where a provider is actually used:
`OR_KEY` for `provider: "openrouter"` models, `OPENAI_API_KEY` for `provider: "openai_responses"`.
For a ready-made openai_responses `tasks.py`, see `examples/offer_gate_decision/`.
```

Replace with:

```markdown
Then `build_golden.py` -> (human validation) -> `run_sweep.py` -> `grade.py` -> `build_report.py`,
the run order in `SKILL.md`. `build_report_html.py` (optional, after `grade.py`) renders the same
numbers plus a per-case pass/parsed/hard/trap grid as a single self-contained `outputs/report.html`
- no server, no build step. API keys are demanded only where a provider is actually used:
`OR_KEY` for `provider: "openrouter"` models, `OPENAI_API_KEY` for `provider: "openai_responses"`.
For a ready-made openai_responses `tasks.py`, see `examples/offer_gate_decision/`.
```

- [ ] **Step 3: Run the full selftest suite to confirm nothing broke**

Run: `python3 templates/selftest.py`
Expected: `all selftest checks passed`, exit 0 (text-only changes; this is a regression check, not a new-behavior check).

- [ ] **Step 4: Commit**

```bash
git add templates/build_report.py README.md
git commit -m "docs: point at grade_agg.json's cases array and build_report_html.py"
```
