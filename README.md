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

The project plans two separate, reusable Skills with supporting tools:

- **Benchmark-case extraction** reconstructs the task from relevant work and
  conversation, producing a self-contained prompt rather than a replay of the
  conversation. It identifies the Git starting state and accounts for relevant
  uncommitted changes without disturbing normal development history or work.
- **Benchmark execution** consumes that case, recreates its starting state in an
  isolated environment, runs a selected agent configuration, and preserves
  artifacts and available metrics for comparison. A runner with agent/provider
  adapters will handle execution and measurement behind the user-facing Skill.

Keeping these responsibilities separate is intended to make cases portable across
agents and model families, while allowing the case format and runner to evolve.

## Status and design

This project is at an early design stage. The extraction Skills, benchmark runner,
and case format are not implemented yet; the workflow above describes the intended
direction.

See the following issues for the project goals and planned implementation:

- [Project vision and goals (#1)](https://github.com/takahirox/work-to-bench/issues/1)
- [Benchmark-case extraction Skill (#2)](https://github.com/takahirox/work-to-bench/issues/2)
- [Benchmark runner Skill and metrics (#3)](https://github.com/takahirox/work-to-bench/issues/3)
