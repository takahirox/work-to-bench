# Benchmark runner

The runner executes one existing case in a new standalone Git workspace and saves
its output. It supports case versions 1 and 2, including nested submodules and
standard Git LFS. It does not evaluate solution quality or modify the source case.

## Requirements and installation

- Python 3.11+, Git 2.43+, and macOS or Linux (process-group termination uses POSIX).
- Git LFS 3.x when the case includes LFS payloads.
- Codex CLI with `exec --json`, `--ephemeral`, and `--ignore-user-config`; tested
  with version 0.154.0. Use existing CLI authentication or authentication supplied
  through your execution environment. Credentials are not copied into run results.
- Install both `run-benchmark` and `extract-benchmark-case` as adjacent Skill
  folders, preserving their scripts. No extra Python packages are needed.

From a checkout, invoke the runner directly:

```sh
python3 skills/run-benchmark/scripts/benchmark_runner.py /path/to/case \
  --output /path/to/runs/my-run \
  --agent codex --model YOUR_MODEL --effort medium --timeout 1800
```

`--model` is required. `--effort` is optional; supported values depend on the model.
An omitted effort uses the agent's default and is recorded as `null`, not guessed.
`--executable` can select a particular installed Codex executable. `--timeout` is a
positive number of seconds for the agent process, excluding preparation and artifact
collection. Existing output directories are rejected. Choose a new path for each
configuration or repetition; completed and failed runs can coexist.

For an installed Skill, ask the agent to use `$run-benchmark` with a case, model,
and destination. Run results remain local unless you explicitly share them.

## Execution and isolation

The runner validates the case, copies the prompt/context and case metadata to an
`inputs/` directory, and restores the exact starting commits under `workspace/`.
The input files are outside the task repository, so they do not pollute its starting
tree or diff. The invocation explains how references to `context/` resolve to those
copied inputs. The submitted prompt is saved as `inputs/invocation.md`.

The Codex adapter uses a workspace-write sandbox, no approval escalation, no tool
network access, and no additional writable roots. Default `/tmp` and `$TMPDIR`
write exceptions are disabled. It ignores personal config and marks the restored
project untrusted so project-local Codex configuration cannot broaden the run.
Hooks, apps, memory, multi-agent delegation, and web search are disabled through
explicit configuration. Host-managed policy and global instructions can still
apply; this is a separate Git workspace with agent-enforced permissions, not a
container or a hermetic operating system. Dependencies and external services are
not provisioned automatically. Tasks requiring unavailable permissions or services
may fail, and those failures are preserved.

The runner does not bypass the agent's sandbox and does not automatically retry a
run, redeem a usage reset, purchase allowance, or switch models/providers. The CLI
may handle its own transport behavior; a usage-limit failure must not trigger a new
run. Programmatic adapters are trusted integrations responsible for enforcing their
own tool permissions. Merely choosing a working directory does not sandbox a
custom adapter.

## Result directory and schema v1

```text
my-run/
  result.json
  inputs/
    case.json
    prompt.md
    invocation.md
    context/               # When provided by the case
  stdout.jsonl              # Raw Codex event stream
  stderr.log
  workspace/                # Preserved final repository and submodule worktrees
  artifacts/
    root/
      changes.patch
      status.txt
      untracked.zlist
    <submodule-path-hash>/  # Same files for each starting submodule
```

`result.json` is written before execution and atomically updated on completion.
Important fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `case_id` | Result format version and source case identifier. |
| `status` | `preparing`, `running`, `completed`, `agent_failed`, `timed_out`, `interrupted`, `runner_failed`, or `artifact_failed`. |
| `agent` | Adapter/provider, CLI version, requested/reported model, and requested reasoning effort. Reported model is `null` if the agent does not expose it. |
| `configuration`, `command` | Timeout and permission settings, plus the exact invocation arguments. No environment variables or credentials are serialized. |
| `starting_repositories` | Exact starting commit for the root and every submodule. |
| `timing` | UTC timestamps and monotonic elapsed seconds; `agent_*` fields isolate agent execution from setup and collection. |
| `exit_code` | Agent process exit code, or `null` if it never started. |
| `usage` | Normalized token totals, missing fields, original per-turn usage objects, and telemetry source. |
| `cost` | Reported charge (currently unavailable), optional USD estimate, reason, and exact supplied pricing table. |
| `logs`, `workspace`, `inputs`, `artifacts` | Run-relative paths and per-repository start/final commits. |
| `warnings`, `errors` | Telemetry gaps or setup/artifact failures. Raw agent details remain in the logs. |

The runner reports `completed` only when the process exits successfully and the
adapter observes a terminal success without a terminal failure or malformed event
stream. This is an execution outcome, not a correctness score.

Tracked-file patches compare the original starting commit to the final working
tree, including committed and uncommitted changes. Untracked paths are listed in
NUL-delimited `untracked.zlist`; their actual contents remain in `workspace/`.
Ignored files also remain in the workspace but are not listed in Git's untracked
inventory. Commits, Git data, LFS caches, executable bits, symlinks, and new files
are retained in place. Submodule diffs are collected separately. An artifact error
is reported explicitly instead of silently losing that repository's summary.

Preparation errors after the output is created produce `runner_failed` with logs
and any successfully restored workspace. Invalid cases and invalid arguments fail
before a run directory is created. Timeout or interruption terminates the process
group and collects partial artifacts. `SIGKILL`, machine shutdown, or storage failure
can interrupt finalization; an unfinished `preparing`/`running` result and existing
files should be inspected rather than treated as a successful run. The runner
never deletes run directories automatically.

CLI exit codes: `0` completed, `124` timed out, `130` interrupted, otherwise `1`.

## Usage and cost

Codex JSONL `turn.completed`/`turn.failed` usage objects are retained verbatim. The
adapter sums known `input_tokens`, `cached_input_tokens`, `output_tokens`, and
`reasoning_output_tokens` across terminal turns. A category absent from any turn is
`null`; missing or malformed telemetry is never treated as free usage. Unknown
provider categories remain in `raw_turns` for later analysis.

Codex input totals include cached input. Reasoning tokens, where exposed, are part
of output tokens; they are not charged a second time by the estimator. No prices
are hard-coded. To calculate an estimate, supply `--pricing /path/to/rates.json`.
For example, this **synthetic** table demonstrates the format, not current pricing:

```json
{
  "model": "YOUR_MODEL",
  "effective_date": "2026-01-01",
  "source": "Replace with the source of the rates you chose",
  "input_usd_per_million": 2,
  "cached_input_usd_per_million": 1,
  "output_usd_per_million": 10
}
```

Rates must be finite and nonnegative and the model must match the requested model.
The estimate uses uncached input × input rate + cached input × cached rate + output
× output rate, divided by one million. It is stored as a decimal string to preserve
precision. Missing usage or a conflicting reported model suppresses the estimate.
Subscription usage is not an API bill; a token-rate estimate is not a reported
charge. Actual model-version identifiers and monetary charges may not be exposed
by the CLI and remain explicitly unavailable.

## Programmatic use and additional adapters

Add this Skill's `scripts/` directory to Python's import path, then call:

```python
from benchmark_runner import run_benchmark

result = run_benchmark(
    "/path/to/case", "/path/to/new-run",
    model="YOUR_MODEL", effort="medium", timeout=1800,
)
```

An adapter has `name` and `provider` attributes and implements:

- `version_command(executable)`: argument vector for a short version query.
- `command(executable, workspace, model, effort)`: argument vector; the runner
  provides the prompt on stdin, streams stdout/stderr to files, and owns lifecycle.
- `parse(stdout_path)`: returns `completed`, `reported_model`, `warnings`, and
  `usage` with normalized `totals` and original `raw_turns`.

Pass an adapter instance via `adapter=` for programmatic use or register its class
in `runner_adapters.ADAPTERS` to expose it through the CLI. Generic lifecycle, case
restoration, timing, and artifact collection do not depend on Codex event shapes.
Adapters must normalize cached input as part of total input and reasoning as part
of total output before using the shared estimator. They must enforce their own
permissions; the shipped Codex adapter is the only integration currently provided.

Official references: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
