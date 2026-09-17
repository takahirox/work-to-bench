---
name: run-benchmark
description: Run existing work-to-bench cases with a selected agent configuration and report saved outputs, execution time, and available usage metrics when the user asks to run or compare benchmarks.
---

# Run a benchmark

Read [the runner guide](references/runner.md) for invocation, adapter behavior,
result fields, pricing, and failure handling. This Skill requires
`extract-benchmark-case` installed alongside it; the runner reuses that Skill's
case validator and restoration helper.

Select the case and agent/model/effort from the user's request and current context.
Ask only if a necessary choice cannot be inferred. Codex requires an explicit
model; external commands use model/effort only if their configuration declares
placeholders. Use a new run directory outside existing Git working trees and outside the case. Do not
change the benchmark task, starting state, or context to improve a result.

Invoke `scripts/benchmark_runner.py` relative to this Skill directory. The shipped
adapters are `codex`, using existing CLI authentication, and `command`, using
`--agent-config` to load a user-selected external command or wrapper. Do not assume
an arbitrary command accepts Codex flags or emits Codex JSONL. Pass user-selected
execution conditions through `--conditions`; omitted settings inherit agent and
environment defaults. Do not invent restrictions or broaden conditions on failure.
Report unsupported conditions and host-policy conflicts without retrying under a
different policy. Distinguish requested/submitted conditions from verified effective
settings; the shipped adapter cannot verify effective settings.

Run only the configurations and repetitions within the user's request. Each run
consumes the selected agent's allowance. The runner does not retry runs or switch
models. On usage limits, stop affected runs and preserve their outputs. Never
redeem reset tickets, purchase allowance, or switch providers/models to evade a
limit without a specific user instruction authorizing that action.

Inspect `result.json`, the exit status, and the saved logs. A completed process is
not proof of a correct solution. Report the run directory, completion status,
agent/model/effort, elapsed execution time, available token counts, and artifact
locations. Distinguish missing metrics from zero, and token-rate cost estimates
from provider-reported charges. Supply `--pricing` only with a matching model's
rates and a recorded source/date; do not invent prices.

Compare runs through their result files, preserved workspaces, and per-repository
diffs. Check submodule artifacts as well as the root diff. Preserve failed,
interrupted, and timed-out runs for inspection. Do not publish logs or generated
changes, delete run directories, or claim an automatic quality ranking unless
that additional work is requested.
