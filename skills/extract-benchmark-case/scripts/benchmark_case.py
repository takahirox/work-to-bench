#!/usr/bin/env python3
"""Package and validate portable benchmark cases using Python's standard library."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class CaseError(ValueError):
    pass


def git(repo, *args, data=None):
    # Avoid inherited Git routing/index variables and optional writes in the source.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
               GIT_AUTHOR_NAME='work-to-bench', GIT_AUTHOR_EMAIL='snapshot@localhost',
               GIT_COMMITTER_NAME='work-to-bench', GIT_COMMITTER_EMAIL='snapshot@localhost')
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                             '-c', 'commit.gpgSign=false', '-C', str(repo), *args],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env)
    if result.returncode:
        # Do not echo host paths, remote URLs or patch content from Git errors.
        raise CaseError('Git operation failed: ' + args[0])
    return result.stdout


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CaseError('Inputs must be regular files, not symbolic links')
    return path.read_bytes()


def file_digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CaseError('Case inputs must be regular files')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def text_input(path):
    data = regular(path)
    try:
        if not data.decode('utf-8').strip():
            raise CaseError('Prompt must not be empty')
    except UnicodeDecodeError as exc:
        raise CaseError('Prompt must be UTF-8') from exc
    return data


def check_tree(repo, commit):
    entries = git(repo, 'ls-tree', '-rz', commit).split(b'\0')
    blobs = set()
    for entry in filter(None, entries):
        info, _ = entry.split(b'\t', 1)
        mode, kind, oid = info.split()
        if mode == b'160000':
            raise CaseError('Submodules are not supported in case format version 1')
        if kind == b'blob':
            blobs.add(oid)
    if blobs:
        sizes = git(repo, 'cat-file', '--batch-check=%(objectname) %(objectsize)',
                    data=b'\n'.join(sorted(blobs)) + b'\n')
        small = [line.split()[0] for line in sizes.splitlines() if int(line.split()[1]) <= 1024]
        if small:
            contents = git(repo, 'cat-file', '--batch', data=b'\n'.join(small) + b'\n')
            offset = 0
            for _ in small:
                end = contents.index(b'\n', offset)
                size = int(contents[offset:end].split()[2])
                blob = contents[end + 1:end + 1 + size]
                offset = end + size + 2
                # Pointer files cannot restore external LFS content from a bundle.
                if blob.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                    raise CaseError('Git LFS pointers are not supported in version 1')


def create(args):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', args.id):
        raise CaseError('ID must be 1–64 lowercase letters, digits, dots, underscores or hyphens')
    if not args.source.strip():
        raise CaseError('Supply a credential-free repository identity')
    repo = Path(args.repo).resolve()
    root = Path(os.fsdecode(git(repo, 'rev-parse', '--show-toplevel')).strip()).resolve()
    output = Path(args.output).absolute()
    resolved_output = output.resolve()
    if resolved_output == root or root in resolved_output.parents:
        raise CaseError('Output must be outside the source working tree')
    if output.exists() or output.is_symlink():
        raise CaseError('Output already exists; choose a new directory')
    base = git(repo, 'rev-parse', '--verify', '--end-of-options', args.base + '^{commit}').decode().strip()
    prompt = text_input(args.prompt)
    context = {}
    for name in args.context:
        path = Path(name)
        if path.name in context:
            raise CaseError('Context filenames must be unique')
        context[path.name] = regular(path)
    patch = regular(args.patch) if args.patch else None
    if patch is not None and not patch.strip():
        raise CaseError('Patch must not be empty; omit --patch for an existing commit')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing cases, including concurrent invocations.
    output.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix='work-to-bench-') as tmp:
            snapshot = Path(tmp)
            object_format = git(repo, 'rev-parse', '--show-object-format').decode().strip()
            git(snapshot, 'init', '--bare', '--object-format=' + object_format)
            git(snapshot, 'fetch', '--no-tags', '--', str(root), base)
            git(snapshot, 'read-tree', base)
            if patch is not None:
                git(snapshot, 'apply', '--cached', '--binary', '--whitespace=nowarn', '-', data=patch)
                tree = git(snapshot, 'write-tree').decode().strip()
                start = git(snapshot, 'commit-tree', tree, '-p', base,
                            data=b'Benchmark starting snapshot\n').decode().strip()
            else:
                start = base
            check_tree(snapshot, start)
            ref = 'refs/heads/benchmark-start'
            git(snapshot, 'update-ref', ref, start)
            git(snapshot, 'bundle', 'create', str(output / 'repository.bundle'), ref)
        (output / 'prompt.md').write_bytes(prompt)
        if context:
            (output / 'context').mkdir()
            for name, data in context.items():
                (output / 'context' / name).write_bytes(data)
        files = ['prompt.md', 'repository.bundle'] + ['context/' + name for name in sorted(context)]
        metadata = {
            'schema_version': 1, 'id': args.id,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'repository': {'identity': args.source, 'base_commit': base,
                           'start_commit': start, 'object_format': object_format,
                           'bundle': 'repository.bundle', 'ref': ref},
            'prompt': 'prompt.md', 'context': files[2:],
            'extraction': {'tool': 'work-to-bench', 'patch_applied': patch is not None},
            'sha256': {name: file_digest(output / name) for name in files},
        }
        (output / 'case.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        validate(output)
    except BaseException:
        shutil.rmtree(output)
        raise
    return metadata


def validate(directory):
    directory = Path(directory).resolve()
    try:
        meta = json.loads(regular(directory / 'case.json'))
        if type(meta['schema_version']) is not int or meta['schema_version'] != 1:
            raise CaseError('Unsupported schema version')
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', meta['id']):
            raise CaseError('Invalid case ID')
        timestamp = datetime.fromisoformat(meta['created_at'])
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise CaseError('Extraction timestamp must use UTC')
        repo = meta['repository']
        if not isinstance(repo['identity'], str) or not repo['identity'].strip():
            raise CaseError('Missing repository identity')
        if repo['object_format'] not in ('sha1', 'sha256'):
            raise CaseError('Unsupported Git object format')
        length = 40 if repo['object_format'] == 'sha1' else 64
        for key in ('base_commit', 'start_commit'):
            if not re.fullmatch('[0-9a-f]{' + str(length) + '}', repo[key]):
                raise CaseError('Invalid commit ID')
        if (meta['prompt'] != 'prompt.md' or repo['bundle'] != 'repository.bundle'
                or repo['ref'] != 'refs/heads/benchmark-start'):
            raise CaseError('Unexpected case file or Git ref')
        if type(meta['extraction']['patch_applied']) is not bool or meta['extraction']['tool'] != 'work-to-bench':
            raise CaseError('Invalid extraction metadata')
        contexts = meta['context']
        if not isinstance(contexts, list) or len(set(contexts)) != len(contexts):
            raise CaseError('Invalid context list')
        for name in contexts:
            if (not isinstance(name, str) or not name.startswith('context/')
                    or len(name.split('/')) != 2 or name.split('/')[1] in ('', '.', '..')
                    or '\\' in name):
                raise CaseError('Invalid context path')
        if contexts and (directory / 'context').is_symlink():
            raise CaseError('Context directory must not be a symbolic link')
        files = ['prompt.md', 'repository.bundle'] + contexts
        if set(meta['sha256']) != set(files):
            raise CaseError('Hash manifest does not match case files')
        for name in files:
            if file_digest(directory / name) != meta['sha256'][name]:
                raise CaseError('Case file hash mismatch: ' + name)
        text_input(directory / 'prompt.md')
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CaseError):
            raise
        raise CaseError('Invalid case metadata') from exc
    with tempfile.TemporaryDirectory(prefix='work-to-bench-verify-') as tmp:
        git(tmp, 'init', '--bare', '--object-format=' + repo['object_format'])
        git(tmp, 'bundle', 'verify', str(directory / repo['bundle']))
        git(tmp, 'fetch', '--no-tags', str(directory / repo['bundle']), repo['ref'])
        start = git(tmp, 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
        if start != repo['start_commit']:
            raise CaseError('Bundle does not contain the specified starting commit')
        git(tmp, 'merge-base', '--is-ancestor', repo['base_commit'], start)
        if not meta['extraction']['patch_applied'] and start != repo['base_commit']:
            raise CaseError('Unpatched case must start at its base commit')
        check_tree(tmp, start)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    new = commands.add_parser('create', help='Create and verify a new benchmark case')
    for arg in ('repo', 'base', 'id', 'source', 'prompt', 'output'):
        new.add_argument('--' + arg, required=True)
    new.add_argument('--patch', help='Reviewed Git patch of prerequisite changes relative to base')
    new.add_argument('--context', action='append', default=[], help='Explicit additional input file (repeatable)')
    check = commands.add_parser('validate', help='Validate case files and independently restore the snapshot')
    check.add_argument('directory')
    args = parser.parse_args()
    try:
        meta = create(args) if args.command == 'create' else validate(args.directory)
    except (CaseError, OSError) as exc:
        print('error: ' + (str(exc) if isinstance(exc, CaseError) else 'File operation failed'), file=sys.stderr)
        return 1
    print(json.dumps({'id': meta['id'], 'start_commit': meta['repository']['start_commit'], 'valid': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
