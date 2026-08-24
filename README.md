# model-swap-eval

A hard fork of [oss-migration-eval](https://github.com/BayramAnnakov/oss-migration-eval) - same
rigorous methodology for deciding whether a cheaper model can replace an expensive one on a
recurring task, extended to also speak a provider's API directly (not just OpenRouter) when
production doesn't route through OpenRouter at all.

## Why a fork, not just a PR

oss-migration-eval only speaks OpenRouter - great for discovering and shortlisting candidates,
but there's no path from "OpenRouter says candidate X wins" back to "does X work through our
actual direct-API calling pattern." General-purpose fixes to the original project land there as
PRs; this fork is where the direct-provider capability and the real-production worked example
live, since they go further than that project's own scope.

## What's here

- Everything from oss-migration-eval: the 8-step methodology, the rigor guardrails
  (`references/rigor.md`), `preflight.py`, `build_report.py`, the full offline `selftest.py` suite.
- A provider abstraction: `MODELS` entries in `tasks.py` are now `{"id": ..., "provider": ...}`
  dicts. `"openrouter"` is unchanged from upstream; `"openai_responses"` calls OpenAI's Responses
  API directly with `strict` JSON-schema structured output. See `references/providers.md`.
- `examples/offer_gate_decision/` - a real worked example evaluating whether a cheaper model can
  replace `LifeOS/practical`'s offer-gate compliance check, built from that project's own
  already-verified test matrix.

## Quick start

Same as upstream (`references/methodology.md`), plus: declare each model's `provider` in
`tasks.py`, and for `"openai_responses"` tasks, supply `"json_schema"` on the task and a dict
(not a string) for each case's `"input"`.

Every template resolves `from tasks import ...` against **its own directory**, so the templates
get copied next to your `tasks.py` and run from there - they are scaffolds, not an installed
package:

```bash
python3 templates/selftest.py            # offline, no API key - run this first, always

mkdir -p work && cp templates/*.py work/ && cd work
$EDITOR tasks.py                         # schema: docstring at the top of build_golden.py
python3 preflight.py validate            # offline schema check, no API key needed
python3 preflight.py estimate            # optional; needs OR_KEY, prices openrouter models only
```

Then `build_golden.py` -> (human validation) -> `run_sweep.py` -> `grade.py` -> `build_report.py`,
the run order in `SKILL.md`. API keys are demanded only where a provider is actually used:
`OR_KEY` for `provider: "openrouter"` models, `OPENAI_API_KEY` for `provider: "openai_responses"`.
For a ready-made openai_responses `tasks.py`, see `examples/offer_gate_decision/`.

## Credits

Methodology by Bayram Annakov ([oss-migration-eval](https://github.com/BayramAnnakov/oss-migration-eval)).
This fork's additions: direct-provider support and the LifeOS worked example.

## License

MIT - see [LICENSE](LICENSE).
