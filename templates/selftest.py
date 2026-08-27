"""selftest.py - offline regression tests for every template's non-network logic.

No API key required (OR_KEY and OPENAI_API_KEY are forced to dummy values throughout - none of the
covered code paths make a real network call). Copies the sibling scripts into an isolated tmp dir per case (matching
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

Plus the honesty/ergonomics regressions caught by the final whole-branch review:
  - grade.py + build_report.py: a cell with UNKNOWN cost (cost=None, e.g. a direct-provider model
    with no tasks.py PRICING entry) must never be reported as $0.0000/call or 100% saved. The
    fabricated savings number is the one business figure this whole tool exists to get right.
  - run_sweep.py: call_openai_responses's request body must have the exact Responses API shape
    (model / instructions / input-as-JSON-string / text.format strict json_schema), tested offline.
  - run_sweep.py + build_golden.py: OR_KEY is required only when a model actually uses provider
    "openrouter"; a missing OPENAI_API_KEY for an "openai_responses" model fails fast instead of
    sending "Bearer None".
  - preflight.py: mixing providers inside one tasks.py is a hard ERROR (the two providers need
    incompatible case "input" shapes), not a soft warning.

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


def run(d, script, env_extra=None, env_remove=()):
    # Both keys are FORCED to a dummy value, never inherited: a real OPENAI_API_KEY in the
    # developer's environment would make these fixtures non-deterministic (green locally, red in
    # CI) and would be the one thing standing between a fixture and a live api.openai.com call.
    env = dict(os.environ, OR_KEY="dummy-not-used", OPENAI_API_KEY="dummy-not-used")
    env.update(env_extra or {})
    for k in env_remove:
        env.pop(k, None)
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
print("\nrun_sweep.py: each provider's key is required only when that provider is actually used")
OPENAI_ONLY_TASKS = '''
FRONTIER = "gpt-5.4-mini"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"}]
SEEDS = [11]
TASKS = [{
    "task": "gate", "kind": "structured", "system": "instructions here", "max_tokens": 50,
    "temperature": 0.0, "json_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
    "cases": [{"id": "c0", "input": {"offer": {"id": "x"}}}],
}]
'''
d = workdir("run_sweep.py")
write(os.path.join(d, "tasks.py"), OPENAI_ONLY_TASKS)
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps([
    {"task": "gate", "case": "c0", "model": "gpt-5.4-mini", "seed": 11, "prompt_hash": ph2,
     "content": "{}", "content_json": {"action": "allow"}, "cost": None},
]))
p = run(d, "run_sweep.py", env_remove=("OR_KEY",))
check("run_sweep.py runs with OR_KEY UNSET when no model uses provider openrouter",
      p.returncode == 0, (p.stdout + p.stderr)[-2000:])
p = run(d, "run_sweep.py", env_remove=("OPENAI_API_KEY",))
check("run_sweep.py fails fast (not 'Bearer None') when OPENAI_API_KEY is missing",
      p.returncode != 0 and "OPENAI_API_KEY" in (p.stdout + p.stderr), (p.stdout + p.stderr)[-2000:])

# ---------------------------------------------------------------------------
print("\nrun_sweep.py: call_openai_responses's request body must have the exact Responses API shape (offline)")
# Importing run_sweep inside this fully-cached fixture dir is a no-op sweep (0 cells to run), so the
# module's body-builder can be called directly with zero network traffic.
write(os.path.join(d, "body_probe.py"), '''
import json, run_sweep
body = run_sweep._build_openai_responses_body(
    "gpt-5.4-mini", "instructions here", {"offer": {"id": "x"}},
    {"type": "object", "properties": {"action": {"type": "string"}}})
print("BODY_JSON=" + json.dumps(body, sort_keys=True))
''')
p = run(d, "body_probe.py")
check("body probe exits 0 (no network)", p.returncode == 0, (p.stdout + p.stderr)[-2000:])
line = next((l for l in p.stdout.splitlines() if l.startswith("BODY_JSON=")), "")
body = json.loads(line[len("BODY_JSON="):]) if line else {}
expected_body = {
    "model": "gpt-5.4-mini",
    "instructions": "instructions here",
    "input": json.dumps({"offer": {"id": "x"}}),
    "text": {"format": {"type": "json_schema", "name": "eval_decision", "strict": True,
                        "schema": {"type": "object", "properties": {"action": {"type": "string"}}}}},
}
check("request body's top-level keys are exactly model/instructions/input/text",
      set(body) == set(expected_body), sorted(body))
check("request body's `input` is a JSON STRING, not a nested object",
      isinstance(body.get("input"), str) and json.loads(body["input"]) == {"offer": {"id": "x"}}, body.get("input"))
check("request body's text.format is strict json_schema named eval_decision, carrying the task schema",
      body.get("text") == expected_body["text"], body.get("text"))
check("request body matches the expected Responses API shape exactly", body == expected_body, body)
shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
print("\nbuild_golden.py: same per-provider key rule (OR_KEY optional, OPENAI_API_KEY enforced)")
d = workdir("build_golden.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "gpt-5.4-mini"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"}]
TASKS = [{
    "task": "gate", "kind": "structured", "system": "s", "max_tokens": 50, "temperature": 0.0,
    "json_schema": {"type": "object"},
    "cases": [{"id": "c%d" % i, "input": {"x": i}, "ref": {"action": "allow"}, "validated": True}
              for i in range(3)],
}]
''')
p = run(d, "build_golden.py", env_remove=("OR_KEY",))     # every case has a ref -> no proposal calls
check("build_golden.py runs with OR_KEY UNSET when no model uses provider openrouter",
      p.returncode == 0, (p.stdout + p.stderr)[-2000:])
p = run(d, "build_golden.py", env_remove=("OPENAI_API_KEY",))
check("build_golden.py fails fast when OPENAI_API_KEY is missing",
      p.returncode != 0 and "OPENAI_API_KEY" in (p.stdout + p.stderr), (p.stdout + p.stderr)[-2000:])
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
print("\npreflight.py validate: mixing providers in one tasks.py must be a hard ERROR, not a warning")
d = workdir("preflight.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "test-vendor/frontier-model"
MODELS = [{"id": FRONTIER, "provider": "openrouter"}, {"id": "gpt-5.4-mini", "provider": "openai_responses"}]
SEEDS = [11, 23, 42]
TASKS = [{
    "task": "t", "kind": "structured", "system": "s", "max_tokens": 10, "temperature": 0.0,
    "json_schema": {"type": "object"},
    # a dict input satisfies the openai_responses model and would crash the openrouter one -
    # no single case shape can serve both, which is why the mix is refused outright
    "cases": [{"id": "c0", "input": {"x": 1}, "ref": {"a": 1}, "validated": True, "hard": True}],
}]
''')
p = subprocess.run([sys.executable, "preflight.py", "validate"], cwd=d,
                    env=dict(os.environ, OR_KEY="dummy-not-used"), capture_output=True, text=True, timeout=30)
check("preflight.py validate exits 1 on a mixed-provider tasks.py", p.returncode == 1, p.stdout)
check("the mixed-provider finding is an ERROR line, not a WARN line",
      any(l.strip().startswith("ERROR") and "mixes providers" in l for l in p.stdout.splitlines()), p.stdout)
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
print("\ngrade.py + build_report.py: an UNKNOWN cost (cost=None) must read n/a - never $0.0000, never 100% saved")
d = workdir("grade.py", "build_report.py")
write(os.path.join(d, "tasks.py"), '''
FRONTIER = "gpt-5.4-mini"
MODELS = [{"id": FRONTIER, "provider": "openai_responses"},
          {"id": "gpt-5.4-nano", "provider": "openai_responses"}]
SEEDS = [11, 23, 42]
PRICING = {"gpt-5.4-mini": {"prompt": 2.5e-7, "completion": 2.0e-6}}   # nano deliberately absent
TASKS = [{
    "task": "gate", "kind": "structured", "system": "s", "max_tokens": 50, "temperature": 0.0,
    "json_schema": {"type": "object"},
    "cases": [{"id": "c0", "input": {"x": 1}, "ref": {"action": "allow"}, "validated": True,
               "ref_fields": ["action"]}],
}]
''')
unpriced_raw = []
for seed in (11, 23, 42):                       # frontier: priced by PRICING -> a real measured cost
    unpriced_raw.append({"task": "gate", "case": "c0", "model": "gpt-5.4-mini", "seed": seed,
                         "content": "{}", "content_json": {"action": "allow"}, "cost": 0.01,
                         "latency_s": 1.2})
for seed, action in ((11, "allow"), (23, "allow"), (42, "deny")):   # candidate: NO PRICING entry -> cost unknown
    unpriced_raw.append({"task": "gate", "case": "c0", "model": "gpt-5.4-nano", "seed": seed,
                         "content": "{}", "content_json": {"action": action}, "cost": None,
                         "latency_s": 0.5})
write(os.path.join(d, "outputs", "seeds_raw.json"), json.dumps(unpriced_raw))

p = run(d, "grade.py")
check("grade.py exits 0 with unpriced cells", p.returncode == 0, p.stderr[-2000:])
nano_cost_line = next((l for l in p.stdout.splitlines()
                       if l.strip().startswith("gpt-5.4-nano") and "saved=" in l), "")
check("grade.py prints a cost line for the unpriced model", bool(nano_cost_line), p.stdout)
check("that line does NOT report a fabricated $0.0000/call", "$" not in nano_cost_line, nano_cost_line)
check("that line does NOT report a fabricated saved=100.0%",
      "saved=  n/a" in nano_cost_line and "100.0%" not in nano_cost_line, nano_cost_line)
check("grade.py names the unknown cost as unknown",
      "n/a (unknown cost)" in nano_cost_line and "UNKNOWN cost" in p.stdout, p.stdout)
agg = json.load(open(os.path.join(d, "outputs", "grade_agg.json")))
nano = agg["cost"]["models"].get("gpt-5.4-nano", {})
mini = agg["cost"]["models"].get("gpt-5.4-mini", {})
check("grade_agg.json records the unpriced model's mean_cost as null, not 0",
      "mean_cost" in nano and nano["mean_cost"] is None, nano)
check("grade_agg.json records the unpriced model's saved_pct as null, not 100",
      "saved_pct" in nano and nano["saved_pct"] is None, nano)
check("grade_agg.json counts the unpriced cells", nano.get("n_unpriced_cells") == 3, nano)
check("the priced frontier still gets a real measured cost", mini.get("mean_cost") == 0.01, mini)

p = run(d, "build_report.py")
check("build_report.py exits 0 with a null cost in grade_agg.json", p.returncode == 0, p.stderr[-2000:])
report = open(os.path.join(d, "outputs", "report.md")).read()
nano_row = next((l for l in report.splitlines() if l.startswith("| gpt-5.4-nano")), "")
check("the report has a row for the unpriced model", bool(nano_row), report)
check("the unpriced model's report row shows n/a, not $0.0000", "n/a" in nano_row and "$0.0000" not in nano_row, nano_row)
check("the unpriced model's report row does NOT claim a 100.0% saving", "100.0%" not in nano_row, nano_row)
check("the report flags the unknown cost instead of totalling it as $0",
      "cost UNKNOWN for gpt-5.4-nano" in report, report)
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
         "n": 3, "parsed": 2, "ok": None, "mae": None, "tolerance": None, "fabricated": None,
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
check("boundary case with parsed<n also surfaces the parsefail marker (not just structured cells)",
      "parsefail" in report and "(parsed 2/3)" in report, report)
check("HARD tag is rendered for the hard case", "HARD" in report, report)
check("Recommendation/Deployment sections stay visibly unfilled (FILL IN present, wrapped in <mark>)",
      report.count("<mark>") >= 6 and "FILL IN" in report, report)
shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("all selftest checks passed")
