# work-to-bench

**work-to-bench** aims to turn real-world AI-assisted work into reusable benchmark
cases, so the same task can be tried with different AI agents, models, and
configurations.

Useful evaluation tasks often emerge during everyday development: requirements
evolve through conversation, context changes, and work may include uncommitted
changes. Capturing those tasks should not require a rigid day-to-day development
workflow. The idea is to work normally and explicitly extract a benchmark only
when a piece of work is worth reproducing or comparing.

## Planned workflow

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
- **Benchmark execution (planned)** consumes that case, recreates its starting state in an
  isolated environment, runs a selected agent configuration, and preserves
  artifacts and available metrics for comparison. A runner with agent/provider
  adapters will handle execution and measurement behind the user-facing Skill.

Keeping these responsibilities separate is intended to make cases portable across
agents and model families, while allowing the case format and runner to evolve.

## Extract a benchmark case

The first implementation provides an explicitly invoked extraction Skill and a
Python helper. It packages a task prompt, an exact Git starting snapshot in a
self-contained bundle, and optional context files. The source repository's working
tree, index, and branches are preserved, including uncommitted work.

Requirements: Python 3.11+ and Git 2.43+. No additional Python packages are needed.

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
so review the content before sharing. Git LFS and submodules are not supported yet.

See the [Skill instructions](skills/extract-benchmark-case/SKILL.md) and
[case format and CLI guide](skills/extract-benchmark-case/references/case-format.md)
for prerequisite patches, context files, validation, and independent restoration.

Run the integration tests with:

```sh
python3 -m unittest discover -s tests -v
```

## Status and design

This project is at an early stage. Extraction and version 1 of the case format are
implemented. The benchmark runner, provider adapters, metrics collection, and
automated evaluation are not implemented yet. Human review determines whether an
extracted candidate faithfully represents the original task.

See the following issues for the project goals and planned implementation:

- [Project vision and goals (#1)](https://github.com/takahirox/work-to-bench/issues/1)
- [Benchmark-case extraction Skill (#2)](https://github.com/takahirox/work-to-bench/issues/2)
- [Benchmark runner Skill and metrics (#3)](https://github.com/takahirox/work-to-bench/issues/3)
