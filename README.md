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
3. **Run the case** in an isolated environment with different agents, models, or
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
  isolated environment, runs a selected agent configuration, and preserves
  artifacts and available metrics for comparison. A runner with agent/provider
  adapters handles execution and measurement behind the user-facing Skill.

Keeping these responsibilities separate is intended to make cases portable across
agents and model families, while allowing the case format and runner to evolve.

## Install the Skills in Codex

You need Python 3.11+ and Git 2.43+. Running benchmarks also requires macOS or
Linux and an authenticated Codex CLI (tested with 0.154.0). Git LFS 3.x is needed
to download missing LFS objects or restore LFS files. No additional Python
packages are required.

### Install for one project

Clone this repository if you do not already have a checkout:

```sh
git clone https://github.com/takahirox/work-to-bench.git /path/to/work-to-bench
```

Replace `/path/to/work-to-bench` with that checkout's absolute path and
`/path/to/your-project` with the target Git project's root. Then copy both complete
Skill folders into the target project:

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

Choose one installation scope to avoid duplicate entries. These commands assume
neither Skill is already installed in the destination; for an update, replace the
existing Skill folders with the complete new versions.

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
allowance. It does not install project dependencies automatically; tool network
access and approval escalation are disabled during the run.

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
usage metrics. The first adapter supports Codex CLI. After following the
[installation and usage guide](#install-the-skills-in-codex) above, you can also
run the helper directly from your work-to-bench checkout:

```sh
python3 skills/run-benchmark/scripts/benchmark_runner.py /path/to/cases/my-task \
  --output /path/to/runs/my-run \
  --agent codex --model YOUR_MODEL --effort medium --timeout 1800
```

Running requires macOS or Linux and an authenticated Codex CLI (tested with
0.154.0), in addition to the extraction requirements above. Each run consumes the
selected agent's allowance. The Codex adapter restricts tool writes to its workspace
and disables tool network access and approval escalation. It does not install
project dependencies automatically.

Inspect `result.json` and `workspace/` in the output directory. Failed, interrupted,
and timed-out runs retain partial results. Missing token/cost metrics are represented
explicitly. An optional `--pricing` table produces a labeled token-rate estimate;
no model prices are hard-coded. Existing output directories are never overwritten.

See the [runner Skill](skills/run-benchmark/SKILL.md) and
[runner guide](skills/run-benchmark/references/runner.md) for permissions, result
fields, pricing, programmatic invocation, and the adapter interface.

## Status and design

This project is at an early stage. Extraction and version 2 of the case format are
implemented; existing version 1 cases remain readable. A benchmark runner with a
Codex adapter records outputs and execution metrics. Additional agent integrations
can be added through adapters. Automated quality evaluation is not implemented;
human review determines task fidelity and result quality.

See the following issues for the project goals and planned implementation:

- [Project vision and goals (#1)](https://github.com/takahirox/work-to-bench/issues/1)
- [Benchmark-case extraction Skill (#2)](https://github.com/takahirox/work-to-bench/issues/2)
- [Benchmark runner Skill and metrics (#3)](https://github.com/takahirox/work-to-bench/issues/3)
