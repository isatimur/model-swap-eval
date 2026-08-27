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
                parsed, n = r.get("parsed"), r.get("n")
                cls = "boundary parsefail" if parsed is not None and n is not None and parsed < n else "boundary"
                extra = (f" <span class='parsefail'>(parsed {parsed}/{n})</span>"
                         if parsed is not None and n is not None and parsed < n else "")
                emit(f"<td class='{cls}'>spread &mdash; {'; '.join(parts)}{extra}</td>")
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
