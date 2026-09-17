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

## Extract a benchmark case

The first implementation provides an explicitly invoked extraction Skill and a
Python helper. It packages a task prompt, an exact Git starting snapshot in a
self-contained bundle, recursive submodule bundles, Git LFS objects, and optional
context files. The source repository's working
tree, index, and branches are preserved, including uncommitted work.

Requirements: Python 3.11+ and Git 2.43+. Git LFS 3.x is needed to download missing
LFS objects or restore LFS files. No additional Python packages are needed.

To use the Skill in Codex, copy `skills/extract-benchmark-case` into your agent's
Skill directory (for example, `~/.codex/skills/`), then start a session where the
Skill is available and explicitly invoke it:

```text
$extract-benchmark-case Capture the task we just worked on as a benchmark case.
```

The Skill infers the task and starting point from the available conversation and
Git state, asking for clarification when needed. Its invocation policy disables
implicit activation. Other agents can follow the same `SKILL.md` instructions
explicitly; their installation and discovery mechanisms may differ.

The helper can also be used directly once you have selected the base and written
a self-contained prompt:

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py create \
  --repo /path/to/source-repository \
  --base <starting-commit> \
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
usage metrics. The first adapter supports Codex CLI. Install both Skill folders
(`run-benchmark` and `extract-benchmark-case`) alongside each other to invoke
`$run-benchmark`, or run the helper from this checkout:

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
