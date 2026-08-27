# Report HTML Viewer — Design

**Status:** approved, pending spec review
**Author:** brainstormed with Timur, 2026-08-27

## Problem

`build_report.py` produces `outputs/report.md` — a tradeoff table and a
statistical reading, good for a decision record but not something a
non-technical stakeholder opens and immediately trusts. Worse: the console
output of `grade.py` computes real per-case detail (pass/parsed counts,
hard/trap-case results, boundary spread, MAE, fabrication counts) that
`grade_agg.json` never persists — `build_report.py` even says so at line
144-148 ("copy the relevant lines from that run here"). The
`entity-brand-matching-eval` pilot showed why this matters: the aggregate
pass-rate said "tied," but two candidate models failed the same hard trap
case in two different, diagnostic ways (one wrong-answered it, one failed
to produce parseable JSON at all) — invisible unless you'd watched the
console scroll by.

model-swap-eval is meant to be a credible, best-in-niche public OSS
project. A visual report that surfaces exactly the finding the methodology
exists to protect — not just the aggregate — is part of that credibility.

## Goals

- A general-purpose HTML report viewer built into model-swap-eval, usable
  by any future eval run, not a one-off for this pilot.
- Self-contained: one `outputs/report.html` file, all data inlined,
  double-click/`file://` opens it — no server, no build step, no new
  dependency. Matches the project's existing "boring scripts" convention
  (`build_report.py` is the template).
- Surfaces per-case detail (pass/parsed/hard/trap), not just the aggregate
  — this is the entire point.
- Never leaks raw production input/output text into a file that might be
  shared or committed by accident.

## Non-goals (v1)

- No filtering/sorting UI, no charting library, no cross-run history or
  diffing between two `outputs/` directories. These are real future scope,
  not v1.
- No change to the subjective/judge panel's aggregate-only reporting —
  `judge_agg.json`'s shape and `build_report.py`'s panel table are
  untouched.

## Design

### 1. `grade.py`: persist per-case detail

`grade.py`'s existing per-case loop (the one that currently only prints)
already computes everything needed. Add a `cases` array to the JSON it
writes to `outputs/grade_agg.json`, one row per `(task, case, model)` —
same aggregation level as the existing pass-rate table (case-clustered,
not per-cell), a **single union shape** across all four task kinds so the
viewer has one loop over one array instead of four branches:

```json
{
  "task": "brand_matching",
  "case_id": "two_same_first_word_no_match",
  "kind": "structured",
  "model": "qwen/qwen3.6-35b-a3b",
  "split": "val",
  "hard": true,
  "trap": false,
  "note": "designed answer: ...",
  "n": 5,
  "parsed": 0,
  "ok": 0,
  "mae": null,
  "tolerance": null,
  "fabricated": null,
  "banned": null,
  "len_violations": null,
  "boundary_spread": null
}
```

Fields populated per kind, everything else `null`:
- `structured` (non-boundary): `parsed`, `ok`
- `structured` (boundary, `ref is None`): `parsed`, `boundary_spread` (the
  existing `Counter`-per-field dict, JSON-safe as-is)
- `numeric`: `parsed` (cells with an extractable number), `mae`,
  `tolerance`, `ok` (within-tolerance count)
- `subjective`: `parsed` (=`n`, always — no parse step), `fabricated`,
  `banned`, `len_violations`

`parsed` and `ok` are both required and kept distinct wherever both apply
— `parsed` (did the model produce usable output at all) is exactly the
signal that caught qwen's failure in the pilot; collapsing it into the
pass fraction would rebuild the same blindness this viewer exists to fix.

`grade_agg.json`'s top-level shape gains one key: `{"deterministic": ...,
"cost": ..., "cases": [...]}`. Existing consumers (`build_report.py`)
ignore the new key; no change needed there for this file to keep working
as it does today — but see Section 3 below for what it gains.

This is aggregated data (counts, means, dispersion of a small set of
category labels) — never a raw model completion string. That's true of
grade.py's existing console printing already; this change persists the
same aggregation, nothing rawer.

### 2. `templates/build_report_html.py` — new script

Mirrors `build_report.py`'s shape and conventions:

- Reads `outputs/grade_agg.json` (now including `cases`) and
  `outputs/judge_agg.json`, plus `tasks.py`, exactly like `build_report.py`
  does. Never reads `outputs/seeds_raw.json` — that file holds raw
  completions and is out of scope for anything this script touches or
  inlines.
- Emits `outputs/report.html`: one file, inline `<style>` (no CSS
  framework), a small inline `<script>` for a details/summary-style
  expand-per-case interaction (plain DOM, no framework, no CDN fetch —
  the file must render identically offline).
- Sections, in order:
  1. Header — same metadata line as `report.md` (date, incumbent, harness
     shape, measured sweep cost, unpriced-model caveat).
  2. Aggregate tradeoff table(s) — deterministic and/or subjective,
     reusing the same numbers `build_report.py` renders, as an HTML table.
  3. Statistical reading — top cluster / clearly-worse, same text as
     `report.md`.
  4. **Per-case grid** (new) — rows = cases (tagged HARD/TRAP where
     applicable), columns = models, each cell shows `ok/n` and, if
     `parsed < n`, a visibly distinct "parsed X/n" flag so a total
     parse failure (the qwen case) is impossible to mistake for a
     graded failure. Boundary cases render their spread dict instead of
     a pass fraction.
  5. Recommendation-per-task and Deployment-notes sections — same
     `<FILL IN>` placeholders as `report.md`, rendered as visibly
     unfilled (e.g. a highlighted `<mark>` or amber box), never silently
     dropped. A viewer with everything filled in, generated by a script,
     would defeat the same guardrail `build_report.py` already protects.
  6. Limitations paragraph — copied verbatim from `report.md`'s text.
- `python3 build_report_html.py` is the whole interface — no flags for
  v1, matching every other template script's zero-config convention.

### 3. `build_report.py`: no behavior change, doc fix only

Since `grade_agg.json` now carries `cases`, the comment at
`build_report.py:144-148` ("copy the relevant lines from that run here")
becomes inaccurate — it should instead point at `build_report_html.py`
for the persisted per-case detail. One-line comment edit, no logic
change; `report.md` stays text-summary-only by design (it's the
decision-record artifact, not the visual one).

### 4. Data-safety statement (explicit, in both scripts' docstrings)

`build_report_html.py` — and, going forward, `grade.py`'s `cases` array —
inline only aggregated counts, means, and small category-label
dispersions. Never a raw model completion string. `outputs/report.html`
inherits `outputs/`'s existing "never commit, may contain
per-engagement data" status in `.gitignore` — no `.gitignore` change
needed, `outputs/` is already blanket-ignored.

### 5. Testing

`build_report_html.py` gets its own `selftest.py` coverage in the same
task that creates it — offline, fixture-driven (a small synthetic
`grade_agg.json`/`judge_agg.json`/`tasks.py`), no network, matching the
existing suite's pattern for `build_report.py`. Checks:
- runs end-to-end and produces valid HTML (parseable, non-empty)
- per-case grid renders one row per case, one column per model
- a `parsed < n` cell is visually/structurally distinguishable from an
  `ok < n` cell (e.g. a distinct CSS class or text marker), not just
  buried in the same number
- `<FILL IN>` sections are present and visibly marked, not silently
  dropped or auto-filled
- boundary-case rows render the spread dict, not a pass fraction

`grade.py`'s existing `selftest.py` fixtures get extended to assert the
new `cases` array's shape and per-kind field population (the four
bulleted field sets in Section 1) for at least one fixture case per kind.

## File changes summary

- Modify: `templates/grade.py` (persist `cases` array)
- Modify: `templates/build_report.py` (one-line comment fix)
- Create: `templates/build_report_html.py`
- Modify: `templates/selftest.py` (extend grade.py fixtures + new
  build_report_html.py fixture tests)
- Modify: `README.md` (mention the HTML viewer alongside the existing
  markdown report step)
