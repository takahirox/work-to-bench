# Case format and command-line guide

Requires Python 3.11+ and Git 2.43+. No Python packages or provider APIs are needed.
Run commands from the repository root, or replace the script path with the installed
Skill's absolute path. The script provides `--help` and `create --help`.

## Create a case

First write a self-contained task prompt to a UTF-8 file. Choose the starting
commit explicitly; the helper does not infer task boundaries or read chat history.

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py create \
  --repo /path/to/source-repository \
  --base <full-starting-commit> \
  --id example-task \
  --source https://github.com/example/project \
  --prompt /path/to/task.md \
  --output /path/to/cases/example-task
```

`--base` accepts a commit or revision resolving to a commit; the case records the
full resolved ID. `--source` is a descriptive, credential-free repository identity
(URL or name), not an executable clone instruction. The helper never discovers or
copies a remote URL automatically. Output must be a new directory outside the
source working tree. Existing output, including empty directories, is rejected.

To include prerequisite changes that are not in the base commit, add
`--patch /path/to/prerequisites.patch`. This must be a Git patch relative to the
selected base, including binary changes where needed. The helper applies it to an
isolated index, creates a snapshot commit there, and leaves source refs, index,
working files, and normal history untouched. A bad patch fails and removes the
new output. No patch means exactly the base commit, regardless of dirty source
files. Ignored and untracked files are never captured automatically.

For example, in a disposable copy containing **only prerequisites**, stage the
selected paths there and use `git diff --cached --binary <base>` to produce a patch.
Never stage or reset the user's real index for extraction. Review the patch to
exclude the task's completed solution. Mixed prerequisite/solution changes require
selection or reconstruction by the agent; the helper cannot identify their intent.

Add `--context /path/to/input.txt` for each explicit extra input. Files are copied
as `context/<basename>`; duplicate basenames and symbolic-link inputs are rejected.
Reference these packaged names from the task prompt. Do not include secrets.

## Version 1 structure

```text
example-task/
  case.json
  prompt.md
  repository.bundle
  context/              # Optional additional input files
```

`case.json` is UTF-8 JSON with these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`; incompatible versions must be rejected. |
| `id` | Stable, human-selected ID: 1–64 lowercase letters, digits, `.`, `_`, or `-`; starts with a letter or digit. |
| `created_at` | Extraction timestamp in ISO 8601 UTC. |
| `repository.identity` | User-supplied repository identity. |
| `repository.base_commit` | Full original commit ID, before an optional prerequisite patch. |
| `repository.start_commit` | Exact full commit ID the benchmark must start from. |
| `repository.object_format` | Git object hash format, `sha1` or `sha256`. |
| `repository.bundle` | Fixed relative path `repository.bundle`. |
| `repository.ref` | Bundle ref `refs/heads/benchmark-start`, pointing to the starting commit. |
| `prompt` | Fixed relative path `prompt.md`; nonempty UTF-8 task prompt. |
| `context` | List of additional relative paths, each `context/<filename>`. |
| `extraction.tool` | `work-to-bench`. |
| `extraction.patch_applied` | Whether a prerequisite patch was applied. |
| `sha256` | Map from each prompt, bundle, and context path to its SHA-256 hash. |

The bundle contains the starting commit and its reachable history, making local
snapshot commits portable without pushing a branch or relying on the source
repository remaining available. **It includes history, not just the starting
files.** Review history and inputs before sharing. Extraction does not publish
anything. The format does not contain evaluation scores or run metrics.

## Validate and restore

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py validate \
  /path/to/cases/example-task
```

Creation performs this validation automatically. Validation checks metadata,
expected files and hashes, verifies the bundle in a fresh bare repository, fetches
its ref, and checks that it resolves to the declared start commit with the base as
an ancestor. It does not execute project code, install dependencies, or evaluate
prompt quality. Successful commands print a JSON summary; failures exit nonzero.

For manual inspection or a future runner, validate first, then restore into a new
directory (use `sha256` instead of `sha1` if specified in `case.json`):

```sh
git init --object-format=sha1 /path/to/new-inspection-directory
git -C /path/to/new-inspection-directory fetch \
  /path/to/cases/example-task/repository.bundle refs/heads/benchmark-start
git -C /path/to/new-inspection-directory checkout --detach FETCH_HEAD
git -C /path/to/new-inspection-directory rev-parse HEAD
```

Compare the printed commit to `repository.start_commit`. Supply `prompt.md` and
listed context separately to the agent. Restoration is an inspection aid, not the
benchmark runner in issue #3. Only run project commands for repositories you trust.

Cases are human-editable. After intentionally editing a prompt or context file,
update its `sha256` entry using `hashlib.sha256(file_bytes).hexdigest()` and validate
again. To change the snapshot or input list, creating a new case is preferable.
Hashes detect accidental changes; they are not signatures or authenticity proofs.

## Initial limits

- Git working repositories only; bare source repositories are not accepted.
- Submodules and Git LFS pointers in the starting tree are rejected because a
  bundle does not include their external content.
- Empty repositories need a real starting commit before extraction.
- The source history must be locally available. Missing objects or incomplete
  shallow history may prevent creation; fetch the needed history separately.
- Additional context consists of regular files, not directories or symbolic links.
- Git snapshots preserve tracked content, symlinks and executable bits, not arbitrary
  filesystem metadata, external services, dependencies, or environment state.
- A forced process termination can leave an incomplete output directory. Remove it
  only after checking its contents, or choose a new output path. Ordinary errors
  clean up newly created output; they never overwrite an existing case.
