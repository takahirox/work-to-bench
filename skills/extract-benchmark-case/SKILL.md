---
name: extract-benchmark-case
description: Extract a portable benchmark case from a Git-based work session when the user explicitly asks to capture work as a benchmark.
---

# Extract a benchmark case

Use this Skill only when explicitly invoked. Produce a benchmark candidate, not
an evaluation or an agent run. Read [the case format and CLI guide](references/case-format.md)
before packaging a case. Resolve helper paths relative to this Skill directory.

## Reconstruct the task

Inspect the relevant conversation, repository instructions, Git history, status,
and diffs. Use only context available in the current session or explicitly provided
by the user; do not search unrelated chat logs. Infer the intended task, including
requirements and decisions made during the work. If the starting point or scope
cannot be determined reliably, ask a focused question before packaging.

Write a self-contained UTF-8 prompt with the objective, requirements, constraints,
necessary context, and observable completion criteria. Distinguish original task
requirements from implementation discoveries. Do not include the completed
solution, incidental conversation, or instructions to imitate the original agent.
Put additional necessary inputs in separate context files and refer to their
packaged paths (`context/<filename>`) from the prompt.

## Select the starting state

Choose an explicit base commit representing the state **before** the extracted
task. Do not default to the current working tree or HEAD merely because they are
convenient. Inspect candidate commits to exclude completed solution changes.

If prerequisites exist only as uncommitted changes, prepare a patch containing
only those prerequisites relative to the base. Review its diff before use. A full
`git diff --binary <base>` includes tracked working-tree changes, including task
solutions; use it only if every change belongs in the starting state. It omits
untracked files. Use a temporary copy/index to construct a selective patch when
needed, never the user's index. Do not stash, reset, switch branches, or commit in
the source repository to extract a case. Preserve staged/unstaged distinctions.

Review selected files and context for credentials, private data, and irrelevant
material. Bundles include the selected commit's reachable Git history, not just
the visible files. Explain this when reporting what was packaged; do not upload
or publish the case unless requested. Never include ignored/untracked files by
default. The helper accepts an explicit patch rather than capturing dirty work
automatically. Git LFS pointers and submodules are unsupported in version 1.

## Package and verify

Use `scripts/benchmark_case.py create` with an explicit base, stable case ID,
prompt file, and new output directory outside the source repository. Supply
`--patch` only for reviewed prerequisites and `--context` for each necessary file.
Supply a credential-free repository identity with `--source`; the helper does
not copy remote URLs or host paths automatically.

Run `scripts/benchmark_case.py validate <case-directory>` to verify the metadata,
file hashes, and independent restoration of the exact Git snapshot. Inspect the
packaged prompt and restored starting tree for task completeness and answer
leakage; mechanical validation cannot determine either.

Report the case location, ID, base and starting commit, included prerequisites and
context, validation result, and any assumptions or limitations. No source refs or
commits are created: a new snapshot, when needed, exists only inside the bundle.
Let the user judge whether the candidate faithfully reproduces the task.
