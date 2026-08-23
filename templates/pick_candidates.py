"""pick_candidates.py - Step 3: live OpenRouter model data + highest-precision provider pins.

Two modes:
  discover : list open-weight models (those with a hugging_face_id) with live pricing,
             newest first - raw material for the shortlist. Cross this with the two live
             leaderboards (artificialanalysis.ai + openrouter.ai/rankings) BY HAND/agent;
             neither has a public API, and hardcoding last month's winners is how evals rot.
  pin      : for each model in tasks.py MODELS, fetch its endpoints and pin the
             highest-precision provider -> provider_pins.json (consumed by run_sweep.py).

Usage:  OR_KEY=<key> python3 pick_candidates.py discover [--max-prompt-price 2.0]
        OR_KEY=<key> python3 pick_candidates.py pin
"""
import os, sys, json, urllib.request

KEY = os.environ["OR_KEY"]
BASE = "https://openrouter.ai/api/v1"
# precision ranking: benchmark the best-served weights, not a quantization lottery
QUANT_RANK = {"fp32": 6, "bf16": 5, "fp16": 5, "fp8": 4, "int8": 3,
              "fp4": 1, "int4": 1, None: 2, "unknown": 2}
CLOSED_PREFIXES = ("anthropic/", "openai/", "google/", "x-ai/")  # first-party serving, no pin needed

def get(path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]

def price(m, kind):  # $/M tokens
    try: return float(m["pricing"][kind]) * 1e6
    except (KeyError, TypeError, ValueError): return float("nan")

def discover():
    cap = float(sys.argv[sys.argv.index("--max-prompt-price") + 1]) if "--max-prompt-price" in sys.argv else None
    models = get("/models")
    ow = [m for m in models if m.get("hugging_face_id")]          # open-weight proxy
    if cap is not None:
        ow = [m for m in ow if price(m, "prompt") <= cap]
    ow.sort(key=lambda m: -(m.get("created") or 0))               # newest first
    print(f"{len(ow)} open-weight models on OpenRouter (of {len(models)} total)\n")
    print(f'{"model":42s} {"$in/M":>7s} {"$out/M":>8s} {"ctx":>8s}')
    for m in ow[:60]:
        print(f'{m["id"]:42s} {price(m,"prompt"):7.2f} {price(m,"completion"):8.2f} '
              f'{m.get("context_length") or 0:8d}')
    print("\nShortlist 4-8 by crossing this with the LIVE quality (artificialanalysis.ai) and"
          "\nusage (openrouter.ai/rankings) leaderboards; span the price range; put the ids in"
          "\ntasks.py MODELS (incumbent FRONTIER included), then run: pick_candidates.py pin")

def pin():
    from tasks import MODELS, FRONTIER
    pins = json.load(open("provider_pins.json")) if os.path.exists("provider_pins.json") else {}
    for model in MODELS:
        if model.startswith(CLOSED_PREFIXES):
            print(f"{model:42s} closed/first-party - no pin needed"); continue
        if model in pins:
            print(f'{model:42s} already pinned -> {pins[model]["provider"]} ({pins[model]["quant"]})'); continue
        try:
            eps = get(f"/models/{model}/endpoints").get("endpoints", [])
        except Exception as e:
            print(f"{model:42s} ENDPOINT FETCH FAILED: {e}"); continue
        if not eps:
            print(f"{model:42s} no endpoints listed - is the id right?"); continue
        # highest precision, tie-break cheapest completion price
        def rank(e):
            q = (e.get("quantization") or "unknown")
            comp = float((e.get("pricing") or {}).get("completion") or 9e9)
            return (-QUANT_RANK.get(q, 2), comp)
        best = sorted(eps, key=rank)[0]
        provider = best.get("provider_name") or best.get("name", "?").split("|")[0].strip()
        quant = best.get("quantization") or "unknown"
        pins[model] = {"provider": provider, "quant": quant}
        others = sorted({(e.get("provider_name") or "?", e.get("quantization") or "?") for e in eps})
        print(f"{model:42s} PIN -> {provider} ({quant})   [all: "
              + ", ".join(f"{p}:{q}" for p, q in others) + "]")
        if quant in ("fp4", "int4", "unknown"):
            print(f'{"":42s} WARNING: best available precision is {quant} - note this in the report')
    json.dump(pins, open("provider_pins.json", "w"), indent=2)
    print(f"\nsaved -> provider_pins.json ({len(pins)} pins). Baseline {FRONTIER} runs unpinned.")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "discover"
    discover() if mode == "discover" else pin()
