# Case format and command-line guide

Requires Python 3.11+ and Git 2.43+. Git LFS 3.x is required for LFS downloads
and LFS restoration; packaging cached objects and validation need no Git LFS
installation. No Python packages or agent-provider APIs are needed.
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
full resolved ID. The root base commit must already be available locally.
`--source` is a descriptive, credential-free repository identity (URL or name),
not an executable clone instruction. The helper does not populate this identity
from configured remote URLs. Output must be a new directory outside the
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

## Submodules and Git LFS

The helper follows each gitlink at the selected starting snapshot, recursively,
and bundles that exact commit from the corresponding local submodule repository.
It does not use the submodule's current HEAD or dirty files as the starting state.
Deinitialized submodules can use their retained Git object storage. To supply a
missing repository explicitly, use `--submodule-source vendor/lib=/path/to/local/repo`.
This accepts a local working or bare repository and can be repeated.

For a reviewed prerequisite change inside a submodule, use
`--submodule-patch vendor/lib=/path/to/prerequisites.patch`. Paths are relative to
the root repository, including nested modules (for example `vendor/lib/nested`).
The patch is relative to the commit selected by the parent gitlink. The helper
creates child snapshots first and updates ancestor gitlinks in isolated commits;
source branches, indexes, configurations, caches, and files are preserved. Mapping
paths absent from the selected tree are errors, not silently ignored options.

Standard SHA-256 Git LFS pointer files are resolved to their content in local LFS
storage, including repository-local `lfs.storage` configuration. Additional caches
can be supplied with `--lfs-object-dir /path/to/objects` (repeatable); both the Git
LFS sharded layout and flat files named by object ID are accepted. This also lets
you supply objects introduced by a prerequisite patch. Object sizes and hashes
must match their pointers. Shared objects are stored only once per case.

Creation defaults to local data. `--fetch-missing` permits retrieving missing
submodule commits, submodule repositories, and LFS objects into temporary storage. Submodule
URLs come from the selected `.gitmodules`; relative URLs resolve against the
parent's `origin` URL (or its local path when there is no origin). LFS uses origin
and repository-local LFS endpoint configuration. Retrieval requires working remote
authentication; user/system Git configuration is intentionally isolated by the
helper. You can instead prepare local repositories/caches with your normal Git
setup and supply the explicit input options. Extraction never pushes data or runs
`submodule update` in the source. Missing or inaccessible objects fail creation.

Only LFS objects needed by the **starting trees** are included. The Git bundles
retain history, but historical LFS contents at other commits are not included.
Repository history can contain URLs in `.gitmodules` and `.lfsconfig`; review those
before sharing as well as the prompt, context, and Git history.

## Version 2 structure

```text
example-task/
  case.json
  prompt.md
  repository.bundle
  submodules/           # One bundle per root-relative submodule path
  lfs/                  # Payloads named by SHA-256 object ID
  context/              # Optional additional input files
```

`case.json` is UTF-8 JSON with these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `2` for new cases; validators also accept existing v1 cases. |
| `id` | Stable, human-selected ID: 1–64 lowercase letters, digits, `.`, `_`, or `-`; starts with a letter or digit. |
| `created_at` | Extraction timestamp in ISO 8601 UTC. |
| `repository.path` | `.` for the root repository. |
| `repository.identity` | User-supplied repository identity. |
| `repository.base_commit` | Full original commit ID, before an optional prerequisite patch. |
| `repository.start_commit` | Exact full commit ID the benchmark must start from. |
| `repository.object_format` | Git object hash format, `sha1` or `sha256`. |
| `repository.bundle` | Fixed relative path `repository.bundle`. |
| `repository.ref` | Bundle ref `refs/heads/benchmark-start`, pointing to the starting commit. |
| `repository.lfs` | List of `{path, oid, size}` records relative to this repository; `oid` is the SHA-256 content hash. |
| `submodules` | Flat list of repository records, with root-relative `path`, `base_commit`, `start_commit`, `object_format`, `bundle`, `ref`, and `lfs`. |
| `prompt` | Fixed relative path `prompt.md`; nonempty UTF-8 task prompt. |
| `context` | List of additional relative paths, each `context/<filename>`. |
| `extraction.tool` | `work-to-bench`. |
| `extraction.patch_applied` | Whether any root or submodule prerequisite patch was applied. |
| `sha256` | Map from each prompt, bundle, LFS object, and context path to its SHA-256 hash. |

Submodule bundle paths are `submodules/<sha256-of-path>.bundle`, where the hash
covers the root-relative path's filesystem encoding. Each submodule `start_commit`
must match its parent's gitlink exactly. LFS payload paths are `lfs/<oid>`; each
must match both the pointer's byte size and its SHA-256 hash. Nested modules use
paths such as `vendor/lib/nested`, and their LFS file paths remain relative to the
nested repository itself. Root `repository.identity` is not duplicated for modules.

Version 1 cases omit `repository.path`, `repository.lfs`, and `submodules`; they
continue to validate and restore without conversion. New readers reject external
objects disguised as v1 metadata, and old v1 readers reject v2 cases.

Each bundle contains the starting commit and its reachable history, making local
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
expected files and hashes, verifies each bundle in a fresh bare repository, and
checks each start commit with its base as an ancestor. It also checks the complete
submodule graph against gitlinks and all LFS records against committed pointers.
Case paths cannot traverse outside the case or pass through symbolic links. It does not execute project code, install dependencies, or evaluate
prompt quality. Successful commands print a JSON summary; failures exit nonzero.

For manual inspection or a future runner, restore into a **new** directory:

```sh
python3 skills/extract-benchmark-case/scripts/benchmark_case.py restore \
  /path/to/cases/example-task --output /path/to/new-inspection-directory
```

Restore validates first, then creates detached working repositories for the root
and each submodule at the recorded commits. It copies verified LFS objects into
the new caches and uses Git LFS checkout to materialize them, preserving executable
bits. Restoration uses only case contents, with no remote downloads or dependency
installation. Existing output directories are never overwritten. Ordinary failures
remove the new output. Custom Git hooks and arbitrary remote-helper protocols are
not enabled by the helper.

Submodules are restored as standalone nested repositories with their own `.git`
directories. Normal inspection and Git status work without contacting the original
remotes. Remote configuration is not reconstructed; a later `submodule update` or
LFS fetch is a separate operation. Supply `prompt.md` and listed context separately
to the agent. Restoration is an inspection aid, not the benchmark runner in #3.

Cases are human-editable. After intentionally editing a prompt or context file,
update its `sha256` entry using `hashlib.sha256(file_bytes).hexdigest()` and validate
again. To change the snapshot or input list, creating a new case is preferable.
Hashes detect accidental changes; they are not signatures or authenticity proofs.

## Initial limits

- Git working repositories only; bare source repositories are not accepted.
- Custom LFS extension transforms depend on external commands/configuration and
  are rejected explicitly; standard LFS pointers and payloads are supported.
- Empty repositories need a real starting commit before extraction.
- The source history must be locally available. Missing objects or incomplete
  shallow history may prevent creation; fetch the needed history separately.
  `--fetch-missing` can retrieve missing submodule commits from origin, but does
  not repair an incomplete shallow history.
- Additional context consists of regular files, not directories or symbolic links.
- Git snapshots preserve tracked content, symlinks and executable bits, not arbitrary
  filesystem metadata, external services, dependencies, or environment state.
- A forced process termination can leave an incomplete output directory. Remove it
  only after checking its contents, or choose a new output path. Ordinary errors
  clean up newly created output; they never overwrite an existing case.
