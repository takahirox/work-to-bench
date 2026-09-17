# work-to-bench

**work-to-bench** aims to turn real-world AI-assisted work into reusable benchmark
cases, so the same task can be tried with different AI agents, models, and
configurations.

Useful evaluation tasks often emerge during everyday development: requirements
evolve through conversation, context changes, and work may include uncommitted
changes. Capturing those tasks should not require a rigid day-to-day development
workflow. The idea is to work normally and explicitly extract a benchmark only
when a piece of work is worth reproducing or comparing.

## Workflow

1. **Work normally** with an AI agent.
2. **Extract an interesting task** into a benchmark case containing a reproducible
   Git starting snapshot, a self-contained task prompt, and any required context.
3. **Run the case** in a separate working directory with different agents, models, or
   configurations, such as reasoning/effort settings where supported.
4. **Capture outputs and execution metrics**, including repository changes, logs,
   elapsed time, and token usage and cost where available.
5. **Compare the results** and decide whether the case is useful to keep. Human
   review is sufficient initially; automatic quality scoring is not required.

## Extraction and execution

The project separates two responsibilities, exposed through reusable Skills (sets
of instructions for an AI agent) and supporting tools:

- **Benchmark-case extraction** reconstructs the task from relevant work and
  conversation, producing a self-contained prompt rather than a replay of the
  conversation. It identifies the Git starting state and accounts for relevant
  uncommitted changes without disturbing normal development history or work.
- **Benchmark execution** consumes that case, recreates its starting state in an
  separate working directory, runs a selected agent configuration, and preserves
  artifacts and available metrics for comparison. A runner with agent/provider
  adapters handles execution and measurement behind the user-facing Skill.

Keeping these responsibilities separate is intended to make cases portable across
agents and model families, while allowing the case format and runner to evolve.

## What work-to-bench handles

A case is a reusable task and its starting repository state. A run is one attempt
at that task by a selected agent. Start with an ordinary development conversation;
you do not need to invent a new project setup or test workflow to extract it.

| Participant | Responsibility |
| --- | --- |
| You, with your assisting agent | Choose the task boundary, prerequisites, inputs, execution conditions, and acceptance criteria. Review whether the extracted case faithfully represents the task. |
| Extraction Skill | Reconstruct a self-contained task from the available conversation, select the pre-task Git state, package explicit inputs, and validate the case. |
| Runner Skill | Select the requested case and agent configuration, invoke the Runner, and explain the saved results. |
| Runner | Validate and restore the case, launch the agent, manage timeouts, and preserve code changes, logs, timing, and available usage metrics. |
| Task agent | Perform the instructions in the case, including setup and project tests when those are part of the task. |
| You or a separate evaluator | Judge correctness using the project's tests and other acceptance checks; compare runs under comparable conditions. |

### Repository state is not the runtime environment

The case packages the selected Git snapshot and reachable history, supported
recursive submodules and Git LFS objects, the task prompt, and explicitly supplied
context files. Prerequisite changes can be included through reviewed patches.
Current dirty files, ignored files, and untracked files are not automatically
captured. See the [case format guide](skills/extract-benchmark-case/references/case-format.md).

It does not copy your whole machine: installed tools, dependency directories,
running databases, browser/GPU state, credentials, and external datasets or
services are not automatically reproduced. A dependency lockfile can travel with
the code; the installed dependencies and access to their registry do not.
Extra context files are inputs, not automatically installed runtime assets.
During a run they live in `inputs/context/`, outside `workspace/`; the invocation
explains how task references to `context/<filename>` resolve.

The restored workspace separates the code from the source checkout. It is not
an automatically provisioned container or a guarantee of filesystem/network
isolation. Access depends on the selected agent, execution conditions, and host
policy; an agent with broad write permissions can modify files outside it.

### Decide where preparation belongs

Reuse the project's existing setup and test instructions. Before extracting a
case, identify the required tool versions, dependencies, services, and input data,
and choose what the task starts with:

- **Prepared environment:** if you want to measure implementation work alone,
  arrange the required runtime tools, accessible dependencies, data, and services
  before launching the Runner, using your own environment setup. Document their
  versions and locations in the case so the task agent knows what is available.
- **Setup as part of the task:** include the existing installation/bootstrap
  commands in the task prompt. The task agent runs them in its restored workspace;
  their time and agent usage are part of the run. Make tools needed to execute
  those commands available beforehand.

The Runner creates a new workspace on each run. Installing dependencies only in
the original checkout does not prepare that workspace. There is no built-in
pre-agent setup hook or dependency provisioner; use externally available resources,
a user-supplied wrapper, or task instructions appropriate to the benchmark.
Wrapper setup is included in the launched process's execution time.

Before running, make external data and services accessible from the execution
host. For portable file inputs, explicitly package each required regular file as
context and explain how the agent should consume or copy it. For resources that
stay external, document their version/identity and access requirements. Keep
credentials in the execution environment rather than the case. Confirm that the
chosen [execution conditions](#select-execution-conditions) permit necessary
network access and writes. A missing prerequisite is an environment problem to
report, not evidence that the agent solved or failed the intended coding task.

### Tests and evaluation answer different questions

Tell the task agent which existing test commands to run and what counts as success.
The Runner does not automatically discover or execute those tests itself. Project
tests can establish specific properties; rendering checks may also require a
browser and reference images, and performance checks may require a defined workload,
hardware, and threshold. State those criteria when they matter to the task.

`result.json` reports execution status and metrics, not a correctness score. A
`completed` run can contain an incorrect solution or failing tests. Review the
actual changes and test evidence, and reproduce checks in the preserved workspace
when needed. The Runner retains agent logs and workspace files, but does not
create a structured project-test report or automatic visual/performance score.
Keep the task, prerequisites, checks, and execution conditions comparable when
comparing agents; unverified effective settings remain a limitation of the comparison.

## Example: reuse an existing project's workflow

Suppose you fixed a CSV import bug with an agent in a JavaScript project. At the
pre-task commit, the project already has `docs/development.md`, a dependency
lockfile, and an `npm test` command. Its setup instructions use `npm ci`. These
names are illustrative: use your project's actual commands and versions.

1. **You and the assisting agent define the start.** Identify the commit before
   the fix and confirm it contains the setup/test instructions. Identify any
   uncommitted prerequisites separately from the completed solution. Assume this
   example requires no prerequisite patch. Choose to include dependency installation
   in the task, while providing the Node/npm versions required by the project on
   the execution host. Select a small failing input, `sample.csv`, and establish
   its expected result from the original requirements.
2. **You invoke the extraction Skill in the original work conversation.** After
   [installing the Skills](#install-the-skills-in-codex), substitute actual paths
   and the pre-task commit in this request:

   ```text
   $extract-benchmark-case Capture the CSV import bug fix we just worked on.
   Use <pre-task-commit> as the base and csv-import as the case ID.
   Include /path/to/inputs/sample.csv as context/sample.csv.
   Preserve the expected import behavior from our agreed requirements.
   In the task, reference docs/development.md for the required Node/npm versions,
   run npm ci, fix the bug, add a regression test using context/sample.csv,
   and run npm test. Use a workspace-local npm cache at .npm-cache for installation
   and testing (set npm_config_cache to that directory). Record the test results
   and any setup failures.
   Save and validate the case at /path/to/benchmarks/cases/csv-import.
   Exclude the completed fix and its solution-specific explanation.
   ```

3. **The extraction Skill packages; you review.** Inspect `prompt.md` for the
   actual bug description, expected output, setup commands, and validation criteria.
   Confirm the starting snapshot predates the solution. Use the
   [restore command](#extract-a-benchmark-case) to inspect it in a separate new
   directory if needed. Mechanical case validation checks packaging integrity,
   not task completeness or whether a solution leaked into the inputs.
4. **You prepare the host and select conditions.** Ensure the specified Node/npm
   versions and authenticated agent are available. For this example, allow the
   registry access needed by `npm ci`. The task uses a workspace-local npm cache
   because the selected sandbox may not allow writes to the usual home-directory
   cache. Verify any other project-specific write requirements as well. For Codex,
   save the following as
   `/path/to/conditions.json`; it is an explicit choice for this run, not a default:

   ```json
   {"sandbox":"workspace-write","network_access":true,"approval_policy":"never"}
   ```

5. **You invoke the Runner Skill; the Runner launches the task agent.** Choose an
   available model in place of `YOUR_MODEL`, with a supported effort setting:

   ```text
   $run-benchmark Run /path/to/benchmarks/cases/csv-import once with Codex.
   Use model YOUR_MODEL, reasoning effort medium, and /path/to/conditions.json.
   Save results to /path/to/benchmarks/runs/csv-import-001.
   ```

   The Runner restores the code and supplies the prompt/context. The task agent
   performs `npm ci`, the implementation, and `npm test`. For another agent, use
   the [external-agent configuration](#run-another-ai-agent); its wrapper must
   implement any requested conditions it declares support for. Case and run output
   directories must be new; keep both outside your source repository, and runs
   outside all existing Git working trees and the case directory.
6. **You or your evaluator inspect the outcome.** Check `result.json`, the agent
   logs, the diff, the new regression test, and the preserved workspace. Check
   that the expected import behavior is tested and that tests actually ran and
   passed. If installation failed, record that separately from correctness of the
   proposed fix. Apply any additional project-specific checks before calling the
   solution correct; choose a new run directory for the next comparison.

## Install the Skills in Codex

You need Python 3.11+ and Git 2.43+. Running benchmarks also requires macOS or
Linux and your selected agent. The Codex adapter requires an authenticated Codex
CLI (tested with 0.154.0); external command integrations do not require Codex. Git LFS 3.x is needed
to download missing LFS objects or restore LFS files. No additional Python
packages are required.

### Install for one project

In the commands below, replace `/path/to/work-to-bench` with an absolute path
for this checkout and `/path/to/your-project` with the target Git project's root.
Choose project-scoped or user-wide installation to avoid duplicate Skill entries.
The copy commands assume neither Skill is already installed in the destination;
for an update, replace the existing folders with the complete new versions.

Clone this repository if you do not already have a checkout:

```sh
git clone https://github.com/takahirox/work-to-bench.git /path/to/work-to-bench
```

Copy both complete Skill folders into the target project:

```sh
cd /path/to/your-project
mkdir -p .agents/skills
cp -R /path/to/work-to-bench/skills/extract-benchmark-case \
      /path/to/work-to-bench/skills/run-benchmark \
      .agents/skills/
```

The installed layout should be:

```text
.agents/skills/
  extract-benchmark-case/
    SKILL.md
    agents/
    references/
    scripts/
  run-benchmark/
    SKILL.md
    agents/
    references/
    scripts/
```

Keep both folders alongside each other: the runner imports the extraction
Skill's validation and restoration helper. Copying only `SKILL.md` is not enough.

### Install for all your projects

As an alternative to project installation, copy the same folders into your
personal Skills directory:

```sh
mkdir -p "$HOME/.agents/skills"
cp -R /path/to/work-to-bench/skills/extract-benchmark-case \
      /path/to/work-to-bench/skills/run-benchmark \
      "$HOME/.agents/skills/"
```

Open Codex in the target project. In Codex CLI or the IDE extension, use `/skills`
or type `$` to select a Skill. Codex detects installed Skills automatically;
restart it if they do not appear. See the
[official Codex Skills documentation](https://developers.openai.com/codex/skills/)
for discovery locations and invocation. Other agents can follow the same
`SKILL.md` instructions explicitly, but their installation mechanisms may differ.

## Use the Skills: extract, run, and inspect

After working on a task in your project, invoke the extraction Skill in the
conversation containing that work. Replace `<pre-task-commit>` with the commit
before the task began, not the commit containing the completed solution:

```text
$extract-benchmark-case Capture the task we just worked on as a benchmark case.
Use <pre-task-commit> as the base commit and my-task as the case ID.
Save it to /path/to/benchmarks/cases/my-task and validate the case.
```

Use absolute paths appropriate for your machine. The case destination must not
already exist and must be outside the source repository. The Skill infers the
task from the available conversation and Git state, asks for clarification when
needed, and packages a self-contained prompt. If prerequisites were uncommitted
at the start, identify them explicitly so only those changes are included, without
the completed solution. Extraction requires explicit invocation.

Review the generated `prompt.md` and starting snapshot, then invoke the runner.
Replace `YOUR_MODEL` with the model you want to use; choose a reasoning effort
supported by that model (the example uses `medium`):

```text
$run-benchmark Run /path/to/benchmarks/cases/my-task once with the Codex adapter.
Use model YOUR_MODEL and reasoning effort medium.
Save the results to /path/to/benchmarks/runs/my-task-001.
```

The run destination must be new, outside existing Git working trees, and outside
the case directory. Choose a different destination for each run. The runner uses
your existing Codex CLI authentication, and each run consumes the selected agent's
allowance. It does not install project dependencies automatically. Execution
conditions are user-selected; omitted settings inherit the agent/environment defaults.

Inspect these paths under `/path/to/benchmarks/runs/my-task-001/`:

| Path | What to inspect |
| --- | --- |
| `result.json` | Completion status, agent/model settings, timing, available token usage, and errors. |
| `workspace/` | The restored repository after execution, including new files and submodules. |
| `artifacts/root/changes.patch` | Tracked-file changes from the starting commit. |
| `artifacts/root/status.txt` | Final Git status; untracked file contents remain in `workspace/`. |
| `artifacts/<submodule-path-hash>/` | Separate changes and status for each starting submodule. |
| `stdout.jsonl` and `stderr.log` | Agent events and diagnostic output. |

A `completed` run is not proof that the solution is correct: review the code and
its validation results. Failed and interrupted runs retain partial outputs for
inspection. Missing usage or cost metrics are unavailable, not zero.

## Extract a benchmark case

The first implementation provides an explicitly invoked extraction Skill and a
Python helper. It packages a task prompt, an exact Git starting snapshot in a
self-contained bundle, recursive submodule bundles, Git LFS objects, and optional
context files. The source repository's working
tree, index, and branches are preserved, including uncommitted work.

To use the helper directly, run the following from your work-to-bench checkout
after selecting the base and writing a self-contained prompt:

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py create \
  --repo /path/to/source-repository \
  --base "<starting-commit>" \
  --id my-task \
  --source example/project \
  --prompt /path/to/task.md \
  --output /path/to/cases/my-task
```

Choose a new output directory outside the source repository. Only the selected
commit is captured unless an explicit prerequisite patch is supplied; current
dirty files are not captured automatically. Bundles include reachable Git history,
so review the content before sharing. Submodules (including nested ones) and LFS
objects needed by the starting snapshot are included. Missing local data produces
an actionable error; `--fetch-missing` allows downloading it into temporary storage.

Restore a case, including its submodules and LFS content, without network access:

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py restore \
  /path/to/cases/my-task --output /path/to/new-working-directory
```

The output must not already exist. Submodule prerequisites can be selected with
`--submodule-patch path/to/module=/path/to/prerequisites.patch`; source worktrees
and indexes are preserved throughout extraction.

See the [Skill instructions](skills/extract-benchmark-case/SKILL.md) and
[case format and CLI guide](skills/extract-benchmark-case/references/case-format.md)
for prerequisite patches, context files, validation, and independent restoration.

Run the integration tests with:

```sh
python3 -m unittest discover -s tests -v
```

## Run a benchmark case

The runner restores each case into a new workspace, runs a selected agent, and
preserves the final repositories, diffs, untracked files, logs, timing, and available
usage metrics. Built-in adapters support Codex CLI and external agent commands. After following the
[installation and usage guide](#install-the-skills-in-codex) above, you can also
run the helper directly from your work-to-bench checkout:

```sh
python3 skills/run-benchmark/scripts/benchmark_runner.py /path/to/cases/my-task \
  --output /path/to/runs/my-run \
  --agent codex --model YOUR_MODEL --effort medium --timeout 1800
```

Running requires macOS or Linux and the selected agent, in addition to the
extraction requirements above. Codex runs require an authenticated Codex CLI
(tested with 0.154.0). Each run consumes the
selected agent's allowance. The Runner does not impose a sandbox, network policy,
or approval policy. It does not install project dependencies automatically.

### Run another AI agent

Use `--agent command` with a JSON configuration to connect a CLI agent or an
API-backed wrapper without changing the Runner. For example, save this as
`/path/to/agent.json`, replacing the command with your agent's actual executable
and arguments:

```json
{
  "name": "my-agent",
  "command": ["/absolute/path/to/my-agent", "run"]
}
```

```sh
python3 skills/run-benchmark/scripts/benchmark_runner.py /path/to/cases/my-task \
  --output /path/to/runs/external-001 \
  --agent command --agent-config /path/to/agent.json
```

The command runs in the restored workspace and receives the task on stdin. For
other input protocols, use a wrapper or `{prompt_file}` argument. Model and effort
are optional unless the configuration uses their placeholders. In a Skill request,
select the command adapter and provide the configuration, case, and output paths.

The Runner retains changes, timing, exit status, `stdout.log`, and `stderr.log`.
Generic command runs do not require Codex JSONL; unavailable usage and cost remain
unknown. Exit zero does not prove a correct solution. See the
[external-agent contract](skills/run-benchmark/references/runner.md#external-agents-through-commands)
for placeholders, optional version queries, and wrappers that support execution
conditions. No condition support is assumed for arbitrary commands.

### Select execution conditions

Pass `--conditions /path/to/conditions.json` to choose execution conditions. For
example, `{"sandbox":"workspace-write","network_access":true,"approval_policy":"never"}`
explicitly enables tool networking in a workspace-write sandbox. With no conditions,
the selected agent inherits its own configuration and host policy. The JSON
example above is for Codex; command integrations require a wrapper that declares
and implements those condition keys. In a Skill invocation, specify
the conditions file alongside the case, model, and destination. Unknown or incompatible
conditions are rejected; the Runner never retries with changed permissions.

Results record requested and submitted conditions separately from effective
conditions, which remain unknown when they cannot be verified. See the
[execution conditions guide](skills/run-benchmark/references/runner.md#execution-conditions)
for supported keys, configuration precedence, and result schema v2. Broader write
permissions can allow the agent to modify files outside the restored workspace.

Inspect `result.json` and `workspace/` in the output directory. Failed, interrupted,
and timed-out runs retain partial results. Missing token/cost metrics are represented
explicitly. An optional `--pricing` table produces a labeled token-rate estimate;
no model prices are hard-coded. Existing output directories are never overwritten.

See the [runner Skill](skills/run-benchmark/SKILL.md) and
[runner guide](skills/run-benchmark/references/runner.md) for permissions, result
fields, pricing, programmatic invocation, and the adapter interface.

## Status and design

This project is at an early stage. Extraction and version 2 of the case format are
implemented; existing version 1 cases remain readable. A benchmark runner with Codex and external-command adapters records outputs and
execution metrics. Agent-specific telemetry can be added through Python adapters. Automated quality evaluation is not implemented;
human review determines task fidelity and result quality.

See the following issues for the project goals and planned implementation:

- [Project vision and goals (#1)](https://github.com/takahirox/work-to-bench/issues/1)
- [Benchmark-case extraction Skill (#2)](https://github.com/takahirox/work-to-bench/issues/2)
- [Benchmark runner Skill and metrics (#3)](https://github.com/takahirox/work-to-bench/issues/3)
