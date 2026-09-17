# Benchmark runner

The runner executes one existing case in a new standalone Git workspace and saves
its output. It supports case versions 1 and 2, including nested submodules and
standard Git LFS. It does not evaluate solution quality or modify the source case.

## Requirements and installation

- Python 3.11+, Git 2.43+, and macOS or Linux (process-group termination uses POSIX).
- Git LFS 3.x when the case includes LFS payloads.
- For the Codex adapter: Codex CLI with `exec --json` (and requested optional flags); tested
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

`--model` is required for Codex. For external commands, model and effort are
required only when their corresponding placeholders are used. `--effort` values
depend on the selected adapter and model.
An omitted effort uses the agent's default and is recorded as `null`, not guessed.
`--executable` can select a particular installed Codex executable. `--timeout` is a
positive number of seconds for the agent process, excluding preparation and artifact
collection. Existing output directories are rejected. Choose a new path for each
configuration or repetition; completed and failed runs can coexist.

For an installed Skill, ask the agent to use `$run-benchmark` with a case, model,
and destination. Run results remain local unless you explicitly share them.

## External agents through commands

Use `--agent command --agent-config /path/to/agent.json` to run an external agent
without modifying this repository or installing Codex. The configuration is a
JSON object. For an agent that reads its task from stdin, an example is:

```json
{
  "name": "my-agent",
  "command": ["/absolute/path/to/my-agent", "run"]
}
```

Replace the executable and arguments with those required by your agent. This is
an argv array, not a shell command: pipes, redirects, and shell expansion are not
performed. Supply a script or wrapper when your agent needs an API, a different
input protocol, or extra setup. The wrapper must wait for the agent to finish and
return a nonzero exit status on failure, including rejected execution conditions.

```sh
python3 skills/run-benchmark/scripts/benchmark_runner.py /path/to/cases/my-task \
  --output /path/to/runs/external-001 \
  --agent command --agent-config /path/to/agent.json --timeout 1800
```

The process runs with the restored workspace as its current directory and receives
the complete invocation on stdin. The manifest supports:

| Key | Meaning |
| --- | --- |
| `name` | Required nonempty integration identity recorded in results. |
| `command` | Required nonempty argv array. |
| `provider` | Optional descriptive provider identity; otherwise unknown. |
| `version_command` | Optional literal argv array, run before the task. Omit if unavailable; a failed explicit probe fails the run. |
| `supported_conditions` | Optional distinct execution-condition keys that the wrapper promises to validate and apply. Default: none. |

Executable paths containing `/` resolve relative to the configuration file;
other executable names use `PATH`. Other relative arguments resolve from the
restored workspace, so use absolute paths for wrapper scripts outside it. Keep
credentials in your authentication environment: the manifest and expanded argv
are saved in results and must not contain secrets.

Command arguments may contain these placeholders, substituted within each argv
entry without shell evaluation:

- `{workspace}`: restored repository directory.
- `{prompt_file}`: absolute path to the UTF-8 invocation also sent on stdin.
- `{context_dir}`: packaged context directory (may not exist if no context was supplied).
- `{conditions_file}`: absolute path to a JSON object containing requested conditions.
- `{model}` and `{effort}`: user-selected values; each must be supplied exactly when
  its placeholder is used. No model or effort is invented for external agents.

Placeholders are not allowed in the executable or `version_command`. Braced
lowercase names are reserved for these placeholders; put complex inline code in
a wrapper file instead. Unknown manifest keys and placeholders are rejected.

For example, a wrapper that supports a network condition and explicit model:

```json
{
  "name": "my-api-wrapper",
  "command": ["python3", "/absolute/path/to/wrapper.py",
              "--task", "{prompt_file}", "--conditions", "{conditions_file}",
              "--model", "{model}"],
  "supported_conditions": ["network_access"]
}
```

Add `--model YOUR_MODEL --conditions /path/to/conditions.json` to the run command.
Declaring condition support requires a `{conditions_file}` argument. The Runner
rejects keys outside the declared support list before restoration. The wrapper
owns value validation, translation, and enforcement for its agent; it must reject
unsupported combinations or host-policy conflicts rather than silently ignore
them. Merely declaring support is not evidence of enforcement, so effective
conditions remain unverified. These integrations are trusted executable code,
not a sandbox provided by the Runner.

Plain stdout is saved as `stdout.log`; stderr is saved as `stderr.log`. Exit zero
means process completion only, never solution correctness. Nonzero exits, launch
errors, timeouts, and interruptions retain available logs and artifacts. Version,
reported model, token usage, and cost remain null when unavailable. The generic
command adapter does not parse optional agent-specific telemetry; use the Python
adapter interface below when structured telemetry is needed. Agent/model choices,
manifest, expanded argv, and requested/submitted conditions are saved for comparison.

## Execution and isolation

The runner validates the case, copies the prompt/context and case metadata to an
`inputs/` directory, and restores the exact starting commits under `workspace/`.
The input files are outside the task repository, so they do not pollute its starting
tree or diff. The invocation explains how references to `context/` resolve to those
copied inputs. The submitted prompt is saved as `inputs/invocation.md`.

### Execution conditions

Use `--conditions /path/to/conditions.json` (or a `conditions` dictionary in
`run_benchmark`) to select execution conditions. Omitting the file or a key means
inherit the agent/environment setting; the Runner does not inject policy defaults.
For example, this deliberately selected configuration enables network access:

```json
{
  "sandbox": "workspace-write",
  "network_access": true,
  "approval_policy": "never",
  "web_search": "live"
}
```

Supported Codex conditions:

| Key | Values |
| --- | --- |
| `sandbox` | `read-only`, `workspace-write`, `danger-full-access` |
| `network_access`, `exclude_slash_tmp`, `exclude_tmpdir_env_var` | Boolean; require explicit `sandbox: workspace-write`. |
| `writable_roots` | List of absolute paths; requires explicit `sandbox: workspace-write`. |
| `approval_policy` | `never`, `on-request` |
| `approvals_reviewer` | `user`, `auto_review` |
| `web_search` | `disabled`, `cached`, `live` |
| `project_trust` | `trusted`, `untrusted` for the restored workspace path. |
| `ignore_user_config`, `ephemeral` | Boolean CLI switches; false leaves the switch absent. |
| `features` | Object with boolean `apps`, `hooks`, `memories`, `multi_agent` entries. |

Unknown keys, invalid values, and contradictory workspace settings fail before
restoration. Workspace-specific conditions require an explicit matching sandbox
so they cannot silently become irrelevant under an inherited sandbox mode.
Conditions are translated to explicit Codex CLI/config overrides. Other settings,
including provider selection, inherit Codex configuration. Personal configuration
is loaded unless explicitly disabled; project configuration depends on Codex trust
and configuration rules. Host-managed policy still takes precedence. The Runner
cannot determine every inherited or effective value. CLI rejections remain failed
runs with diagnostics, without fallback. Interactive approval support depends on
the agent's non-interactive interface; the Runner does not answer approval prompts.

Result schema v2 records `configuration.execution_conditions.requested`, the
supported keys, and `submitted` (null until agent launch succeeds). Submitted
means passed to the process, not accepted or enforced. `effective` remains null
and `verification` is `unverified`; compare explicit conditions and account for
unverified inherited settings when comparing results. Version 1 results used a
fixed policy summary and must not be interpreted as verified enforcement.

Restoration and artifact collection stay in a separate workspace. This is not a
container or a hermetic environment; an agent granted broader permissions can
write outside it. Dependencies and external services are not provisioned
by the Runner. Git-routing variables are removed from the child environment to
avoid accidentally directing operations to the source repository; other environment
variables are inherited. The input prompt is preserved for inspection.

The runner does not bypass the agent's sandbox and does not automatically retry a
run, redeem a usage reset, purchase allowance, or switch models/providers. The CLI
may handle its own transport behavior; a usage-limit failure must not trigger a new
run. Programmatic adapters are trusted integrations responsible for enforcing their
own tool permissions. Merely choosing a working directory does not sandbox a
custom adapter.

## Result directory and schema v2

```text
my-run/
  result.json
  inputs/
    case.json
    prompt.md
    invocation.md
    conditions.json         # Requested execution conditions
    context/               # When provided by the case
  stdout.jsonl              # Raw Codex events; stdout.log for command integrations
  stderr.log
  workspace/                # Preserved final repository and submodule worktrees
  artifacts/
    root/
      changes.patch
      status.txt
      untracked.zlist
    <submodule-path-hash>/  # Same files for each starting submodule
```

The run directory is created with owner-only permissions. `result.json` is written
before execution and atomically updated on completion.
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
| `cost` | Optional agent-reported USD charge, optional token-rate estimate, estimate availability reason, and exact supplied pricing table. Codex does not report a charge. |
| `logs`, `workspace`, `inputs`, `artifacts` | Run-relative paths and per-repository start/final commits. |
| `warnings`, `errors` | Telemetry gaps or setup/artifact failures. Raw agent details remain in the logs. |

The runner reports `completed` only when the process exits successfully and the
adapter observes its completion condition. For Codex, this requires a terminal
success without a terminal failure, unfinished final turn, or malformed event
stream. For command integrations, completion uses the process exit status. This is an execution outcome, not a correctness score.

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

An adapter has `name` and `provider` attributes. It may expose `supported_conditions` and `configure(conditions)` to validate and
store execution settings. Without `configure`, nonempty conditions are rejected.
It implements:

- `version_command(executable)`: argument vector for a short version query, or
  `None` if unavailable.
- `command(executable, workspace, model, effort)`: argument vector; the runner
  provides the prompt on stdin, streams stdout/stderr to files, and owns lifecycle.
- `parse(stdout_path)`: returns `completed`, `reported_model`, `warnings`, and
  `usage` with normalized `totals` and original `raw_turns`. An optional
  `reported_cost_usd` field preserves an agent-reported charge as a decimal string.
  Invalid adapter telemetry produces an explicit warning and unavailable metrics;
  it does not prevent the final result and partial artifacts from being saved.

Pass an adapter instance via `adapter=` for programmatic use or register its class
in `runner_adapters.ADAPTERS` to expose it through the CLI. Generic lifecycle, case
restoration, timing, and artifact collection do not depend on Codex event shapes.
Adapters must normalize cached input as part of total input and reasoning as part
of total output before using the shared estimator. They must enforce their own
permissions; the Codex adapter and generic command adapter are the built-in integrations.
Optional `validate_selection(model, effort)` rejects unsupported model/effort
choices before restoration. Codex telemetry parsing is not required by the Runner.

Official references: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
