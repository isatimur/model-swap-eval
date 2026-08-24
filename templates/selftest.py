"""selftest.py - offline regression tests for every template's non-network logic.

No API key required (OR_KEY is set to a dummy value throughout - none of the covered code paths
make a real network call). Copies the sibling scripts into an isolated tmp dir per case (matching
real usage: templates are copied alongside a user's tasks.py and run from that directory - the
scripts resolve `from tasks import ...` against their own directory, not the caller's cwd).

Note on the openai_responses run_sweep.py fixture below: its no-network guarantee holds only
because the fixture's outputs/seeds_raw.json cell carries a prompt_hash that matches run_sweep.py's
own prompt_hash() for that task/case. If that hash ever mismatches (e.g. the two payloads drift out
of sync), run_sweep.py will treat the cell as not-yet-run and call_retry() will attempt up to
several real HTTPS calls to api.openai.com with a dummy key, with retry/backoff sleeps that can add
up to 16+ cumulative seconds - which could be slow, but the "0 cells to run" check below will still
eventually report a failure (or, in the worst case, the subprocess call could time out and crash
the harness rather than failing cleanly, since run() uses a 30s subprocess timeout with no
exception handling around it).

Covers three regressions caught by an upstream audit:
  1. grade.py: a numeric-kind task falling back to a golden_proposed.json reference (no "ref" in
     tasks.py) must actually get graded, not silently skipped (the fallback ref used to be handed
     to the numeric branch as raw text instead of a parsed number).
  2. build_golden.py: growing a task's cases a few at a time must not permanently starve the "val"
     split (the old code sized the 50/30/20 ratio off each new batch alone, not the task's running
     total, so small/incremental batches rounded val down to zero forever).
  3. run_sweep.py: errored cells must be retried on resume, and a prompt/param edit must not
     silently reuse a stale cached cell.

Plus coverage for three additive tools built on top of those fixes:
  - preflight.py validate: every injected schema mistake must be caught, and a well-formed
    tasks.py must pass clean.
  - grade.py / judge.py: the structured outputs/grade_agg.json and outputs/judge_agg.json they
    persist must round-trip correctly.
  - build_report.py: the assembled report must carry real computed numbers through correctly,
    and must NEVER auto-decide the business call (verdict / per-task recommendation stay
    placeholders - that judgment belongs to the user, per rigor.md).

Usage:  python3 selftest.py   (exit 0 = all pass)
"""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def workdir(*scripts):
    d = tempfile.mkdtemp(prefix="oss-migration-eval-selftest-")
    for s in scripts:
        shutil.copy(os.path.join(HERE, s), os.path.join(d, s))
    return d


def run(d, script, env_extra=None):
    env = dict(os.environ, OR_KEY="dummy-not-used")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, script], cwd=d, env=env,
                           capture_output=True, text=True, timeout=30)


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
print("grade.py: numeric golden-fallback reference must be parsed and graded")
d = workdir("grade.py")

write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "test-vendor/cheap-model", "provider": "openrouter"}]
SEEDS = [11, 23, 42]

TASKS = [
    {
        "task": "extract", "kind": "structured", "system": "extract",
        "max_tokens": 100, "temperature": 0.0,
        "cases": [{
            "id": "case1", "input": "irrelevant",
            "ref": {"tier": "Hot", "total": 89}, "validated": True,
            "ref_fields": ["tier"], "tolerance": {"total": 5},
        }],
    },
    {
        "task": "score", "kind": "numeric", "system": "score it",
        "max_tokens": 50, "temperature": 0.0,
        "cases": [{
            "id": "num1", "input": "irrelevant",
            # no "ref": falls back to outputs/golden_proposed.json - the regression path
            "tolerance": 2,
        }],
    },
]
''')

seeds_raw = []
for model, tier_total in [("test-vendor/frontier-model", ("Hot", 89)), ("test-vendor/cheap-model", ("Hot", 90))]:
    for seed in (11, 23, 42):
        seeds_raw.append({"task": "extract", "case": "case1", "model": model, "seed": seed,
                           "content": json.dumps({"tier": tier_total[0], "total": tier_total[1]}), "cost": 0.001})
for model, val in [("test-vendor/frontier-model", "42"), ("test-vendor/cheap-model", "41")]:
    for seed in (11, 23, 42):
        seeds_raw.append({"task": "score", "case": "num1", "model": model, "seed": seed,
                           "content": f"the score is {val}", "cost": 0.001})
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps(seeds_raw))
write(os.path.join(d, "outputs", "golden_proposed.json"), json.dumps({
    "score/num1": {"split": "dev", "hard": False, "proposals": ["the score is 42"],
                   "ref_raw": "the score is 42", "human_validated": True},
}))

p = run(d, "grade.py")
check("grade.py exits 0", p.returncode == 0, p.stderr[-2000:])
check("no REFUSING (refs are validated)", "REFUSING" not in p.stdout, p.stdout)
check("numeric case is NOT skipped (the regression)", "SKIP: numeric case has no numeric ref" not in p.stdout, p.stdout)
check("numeric case reports MAE", "MAE=" in p.stdout, p.stdout)
agg_path = os.path.join(d, "outputs", "grade_agg.json")
check("grade.py writes outputs/grade_agg.json", os.path.exists(agg_path))
if os.path.exists(agg_path):
    agg = json.load(open(agg_path))
    check("grade_agg.json has a deterministic section", bool(agg.get("deterministic")), agg)
    check("grade_agg.json has a cost section with both models",
          set(agg.get("cost", {}).get("models", {})) == {"test-vendor/frontier-model", "test-vendor/cheap-model"}, agg)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nbuild_golden.py: incremental case growth must not starve the val split")
d = workdir("build_golden.py")


def tasks_py(n):
    cases = ",\n".join(
        f'{{"id": "c{i}", "input": "x", "ref": {i}, "validated": True}}' for i in range(n)
    )
    return f'''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{{"id": FRONTIER, "provider": "openrouter"}}, {{"id": "test-vendor/cheap-model", "provider": "openrouter"}}]
TASKS = [{{
    "task": "t", "kind": "numeric", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [{cases}],
}}]
'''


write(os.path.join(d, "tasks.py"), tasks_py(3))
p1 = run(d, "build_golden.py")
check("build_golden.py (3 cases) exits 0", p1.returncode == 0, p1.stderr[-2000:])
splits_after_3 = json.load(open(os.path.join(d, "outputs", "splits.json")))

write(os.path.join(d, "tasks.py"), tasks_py(5))     # grow the same task by 2 more cases
p2 = run(d, "build_golden.py")
check("build_golden.py (5 cases, incremental) exits 0", p2.returncode == 0, p2.stderr[-2000:])
splits_after_5 = json.load(open(os.path.join(d, "outputs", "splits.json")))

check("prior 3 splits preserved across the incremental run",
      all(splits_after_5[k] == v for k, v in splits_after_3.items()),
      f"before={splits_after_3} after={splits_after_5}")
counts = {}
for v in splits_after_5.values():
    counts[v] = counts.get(v, 0) + 1
check("val split is non-empty after incremental growth (the regression)",
      counts.get("val", 0) >= 1, f"counts={counts}")
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nrun_sweep.py: a fully-covered, correctly-hashed cache must resume with zero network calls")
import hashlib


def prompt_hash(system, inp, max_tokens, temperature, json_schema=None):   # must mirror run_sweep.py's prompt_hash()
    payload = json.dumps([system, inp, max_tokens, temperature, json_schema], sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


d = workdir("run_sweep.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "test-vendor/cheap-model", "provider": "openrouter"}]
SEEDS = [11, 23]
TASKS = [{
    "task": "t", "kind": "numeric", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [{"id": "c0", "input": "x"}],
}]
''')
ph = prompt_hash("s", "x", 10, 0.0)
cells = [{"task": "t", "case": "c0", "model": m, "seed": s, "prompt_hash": ph, "content": "1", "cost": 0.0}
         for m in ("test-vendor/frontier-model", "test-vendor/cheap-model") for s in (11, 23)]
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps(cells))

p = run(d, "run_sweep.py")
check("run_sweep.py exits 0 with a fully-cached fixture (no network needed)", p.returncode == 0, p.stderr[-2000:])
check("resume recognizes all 4 cells as already present", "resuming: 4 cells already present" in p.stdout, p.stdout)
check("0 cells left to run (hash matched, nothing stale/errored)", "0 cells to run" in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nrun_sweep.py: an openai_responses-only tasks.py must load and resume correctly with a pre-cached cell (no network call)")
d = workdir("run_sweep.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "gpt-5.4-mini"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"}]
SEEDS = [11]
TASKS = [{
    "task": "gate", "kind": "structured", "system": "instructions here", "max_tokens": 50,
    "temperature": 0.0, "json_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
    "cases": [{"id": "c0", "input": {"offer": {"id": "x"}}}],
}]
''')
ph2 = prompt_hash("instructions here", {"offer": {"id": "x"}}, 50, 0.0,
                   {"type": "object", "properties": {"action": {"type": "string"}}})
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps([
    {"task": "gate", "case": "c0", "model": "gpt-5.4-mini", "seed": 11, "prompt_hash": ph2,
     "content": "{}", "content_json": {"action": "allow"}, "cost": 0.0},
]))
# Pre-seeding the one job as an already-cached, correctly-hashed cell (rather than pointing this at a
# real endpoint) is the only way to prove MODELS-parsing and resume/hash-matching don't break for an
# openai_responses entry, without spending anything or risking a live call. Because the cell is
# pre-cached, `jobs` ends up empty here and call_for_model() (the actual provider-routing/dispatch
# function) is never invoked - this does NOT exercise dispatch/routing to openai_responses.
p = run(d, "run_sweep.py")
check("run_sweep.py exits 0 for an openai_responses-only tasks.py", p.returncode == 0, p.stderr[-2000:])
check("resume recognizes the cached cell for the openai_responses model",
      "resuming: 1 cells already present" in p.stdout, p.stdout)
check("0 cells to run - fixture loads and resumes with zero jobs left (dispatch/routing not exercised)",
      "0 cells to run" in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\npreflight.py validate: must catch every injected schema error, and pass a clean tasks.py")
d = workdir("preflight.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "test-vendor/cheap-model", "provider": "openrouter"}]
SEEDS = [11, 23]
TASKS = [{
    "task": "t", "kind": "numeric", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [
        {"id": "c0", "input": "x", "ref": 5},
        {"id": "c0", "input": "y"},
    ],
}]
''')
p = subprocess.run([sys.executable, "preflight.py", "validate"], cwd=d,
                    env=dict(os.environ, OR_KEY="dummy-not-used"), capture_output=True, text=True, timeout=30)
check("preflight.py validate exits 1 on a broken tasks.py", p.returncode == 1, p.stdout)
check("catches too-few seeds", "SEEDS has only 2" in p.stdout, p.stdout)
check("catches duplicate case ids", "duplicate case id" in p.stdout, p.stdout)
check("catches an unvalidated pasted ref", 'no "validated": true' in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)

d = workdir("preflight.py")
clean_cases = ",\n".join(
    f'{{"id": "c{i}", "input": "input {i}", "ref": {i}, "validated": True, "hard": {i == 0}}}'
    for i in range(10)
)
write(os.path.join(d, "tasks.py"), f'''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{{"id": FRONTIER, "provider": "openrouter"}}, {{"id": "test-vendor/cheap1", "provider": "openrouter"}}, {{"id": "test-vendor/cheap2", "provider": "openrouter"}}, {{"id": "test-vendor/cheap3", "provider": "openrouter"}}]
SEEDS = [11, 23, 42, 77, 101]
TASKS = [{{
    "task": "t", "kind": "numeric", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [{clean_cases}],
}}]
''')
p = subprocess.run([sys.executable, "preflight.py", "validate"], cwd=d,
                    env=dict(os.environ, OR_KEY="dummy-not-used"), capture_output=True, text=True, timeout=30)
check("preflight.py validate exits 0 on a well-formed tasks.py", p.returncode == 0, p.stdout)
check("reports 0 errors on the clean fixture", "0 error(s)" in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\npreflight.py validate: must catch a missing json_schema and a provider/input-type mismatch")
d = workdir("preflight.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"}]
SEEDS = [11, 23, 42]
TASKS = [{
    "task": "t", "kind": "structured", "system": "s", "max_tokens": 10, "temperature": 0.0,
    # no "json_schema" - required when a model uses provider "openai_responses"
    "cases": [
        {"id": "c0", "input": "this should be a dict, not a string"},
    ],
}]
''')
p = subprocess.run([sys.executable, "preflight.py", "validate"], cwd=d,
                    env=dict(os.environ, OR_KEY="dummy-not-used"), capture_output=True, text=True, timeout=30)
check("preflight.py validate exits 1 on a broken openai_responses tasks.py", p.returncode == 1, p.stdout)
check("catches missing json_schema", "json_schema" in p.stdout, p.stdout)
check("catches a string \"input\" where openai_responses needs a dict", "must be a dict" in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\ngrade.py: content_json must be trusted over garbled raw content text")
d = workdir("grade.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "gpt-5.4-mini"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"}]
SEEDS = [11]
TASKS = [{
    "task": "t", "kind": "structured", "system": "s", "max_tokens": 50, "temperature": 0.0,
    "json_schema": {"type": "object"},
    "cases": [{"id": "c0", "input": {"x": 1}, "ref": {"action": "allow"}, "validated": True,
               "ref_fields": ["action"]}],
}]
''')
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps([
    {"task": "t", "case": "c0", "model": "gpt-5.4-mini", "seed": 11,
     "content": "not parseable as json at all", "content_json": {"action": "allow"}, "cost": 0.0},
]))
p = run(d, "grade.py")
check("grade.py exits 0", p.returncode == 0, p.stderr[-2000:])
check("trusts content_json over unparseable raw content (the regression)", "pass 1/1" in p.stdout, p.stdout)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nbuild_report.py: must assemble a report from grade_agg.json (+ judge_agg.json) without deciding anything")
d = workdir("build_report.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [FRONTIER, "test-vendor/cheap-model"]
SEEDS = [11, 23, 42]
TASKS = [{
    "task": "score", "kind": "numeric", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "cases": [{"id": "c0", "input": "x"}],
}]
''')
write(os.path.join(d, "outputs", "grade_agg.json"), json.dumps({
    "deterministic": {"n_cases": 1, "mdd_pp": None, "models": {
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
}))
p = run(d, "build_report.py")
check("build_report.py exits 0", p.returncode == 0, p.stderr[-2000:])
report_path = os.path.join(d, "outputs", "report.md")
check("writes outputs/report.md", os.path.exists(report_path))
report = open(report_path).read() if os.path.exists(report_path) else ""
check("tradeoff table shows the frontier's real pass-rate", "100.0%" in report, report)
check("tradeoff table shows the cheap model's real pass-rate", "50.0%" in report, report)
check("cheap model is correctly bucketed as clearly worse", "cheap-model" in report and "Clearly worse:** cheap-model" in report, report)
check("cost savings figure is carried through", "80.0%" in report, report)
check("the business-call Verdict is NOT auto-decided (still a placeholder)", "<FILL IN" in report, report)
check("the per-task recommendation is NOT auto-decided (still a placeholder)",
      report.count("<FILL IN") >= 5, report)  # verdict + validator + 4 recommendation-table cells
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all selftest checks passed")
