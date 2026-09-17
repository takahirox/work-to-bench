#!/usr/bin/env python3
"""Package and validate portable benchmark cases using Python's standard library."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import posixpath
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit


class CaseError(ValueError):
    pass


def git(repo, *args, data=None, optional=False):
    # Avoid inherited Git routing/index variables and optional writes in the source.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
               GIT_LFS_SKIP_SMUDGE='1', GIT_NO_LAZY_FETCH='1',
               GIT_AUTHOR_NAME='work-to-bench', GIT_AUTHOR_EMAIL='snapshot@localhost',
               GIT_COMMITTER_NAME='work-to-bench', GIT_COMMITTER_EMAIL='snapshot@localhost')
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                             '-c', 'commit.gpgSign=false', '-c', 'protocol.allow=never',
                             '-c', 'protocol.file.allow=always', '-c', 'protocol.https.allow=always',
                             '-c', 'protocol.http.allow=always', '-c', 'protocol.ssh.allow=always',
                             '-c', 'protocol.git.allow=always',
                             '-C', str(repo), *args],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env)
    if optional and result.returncode == 1:
        return None
    if result.returncode:
        # Do not echo host paths, remote URLs or patch content from Git errors.
        command = 0
        while args[command] == '-c':
            command += 2
        raise CaseError('Git operation failed: ' + args[command])
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


def safe_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(part in ('', '.', '..') or part.lower() == '.git'
                   for part in value.split('/')) or ':' in value or '\0' in value):
        raise CaseError('Unsafe relative path')
    return value


def case_file(directory, name):
    safe_path(name)
    target = directory
    for part in name.split('/'):
        target = target / part
        if target.is_symlink():
            raise CaseError('Case paths must not contain symbolic links')
    return target


def inspect_tree(repo, commit):
    """Return gitlinks and LFS pointers from committed blobs, never working files."""
    modules, blobs, lfs = {}, {}, []
    for entry in filter(None, git(repo, 'ls-tree', '-rz', commit).split(b'\0')):
        info, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = info.split()
        path = os.fsdecode(raw_path)
        safe_path(path)
        if mode == b'160000':
            modules[path] = oid.decode()
        elif kind == b'blob' and mode in (b'100644', b'100755'):
            blobs.setdefault(oid, []).append(path)
    if blobs:
        sizes = git(repo, 'cat-file', '--batch-check=%(objectname) %(objectsize)',
                    data=b'\n'.join(sorted(blobs)) + b'\n')
        small = [line.split()[0] for line in sizes.splitlines() if int(line.split()[1]) < 1024]
        if small:
            contents = git(repo, 'cat-file', '--batch', data=b'\n'.join(small) + b'\n')
            offset = 0
            for oid in small:
                end = contents.index(b'\n', offset)
                size = int(contents[offset:end].split()[2])
                blob = contents[end + 1:end + 1 + size]
                offset = end + size + 2
                if not blob.startswith((b'version https://git-lfs.github.com/spec/v1\n',
                                        b'version https://hawser.github.com/spec/v1\n')):
                    continue
                match = re.fullmatch(
                    rb'version [^\n]+\n(?:ext-[^\n]+\n)*oid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n', blob)
                if not match:
                    raise CaseError('Malformed LFS pointer')
                if b'\next-' in blob:
                    raise CaseError('LFS extension transforms require external configuration and are not portable')
                for path in blobs[oid]:
                    lfs.append({'path': path, 'oid': match[1].decode(), 'size': int(match[2])})
    return modules, sorted(lfs, key=lambda item: item['path'])


def config(repo, key):
    value = git(repo, 'config', '--get', key, optional=True)
    return os.fsdecode(value).rstrip('\n') if value is not None else None


def module_config(repo, commit):
    if not git(repo, 'ls-tree', commit, '--', '.gitmodules'):
        return {}
    raw = git(repo, 'config', '--null', '--blob', commit + ':.gitmodules',
              '--get-regexp', r'^submodule\..*\.path$', optional=True)
    result = {}
    for item in filter(None, (raw or b'').split(b'\0')):
        key, path = os.fsdecode(item).split('\n', 1)
        url = git(repo, 'config', '--blob', commit + ':.gitmodules', '--get',
                  key[:-4] + 'url', optional=True)
        result[path] = (key[len('submodule.'):-len('.path')],
                        os.fsdecode(url).rstrip('\n') if url is not None else None)
    return result


def local_url(value, source):
    """Keep network URLs intact and anchor relative local URLs before changing cwd."""
    if not value or ':' in value:
        return value
    return str((source / value).resolve())


def origin_url(source):
    return local_url(config(source, 'remote.origin.url'), source)


def resolve_module_url(url, parent):
    if not url or not url.startswith(('./', '../')):
        return url
    if not parent:
        raise CaseError('Relative submodule URL needs a parent remote or --submodule-source')
    if '://' in parent:
        parsed = urlsplit(parent)
        return urlunsplit(parsed._replace(path=posixpath.normpath(parsed.path + '/' + url)))
    if ':' in parent and not Path(parent).is_absolute():
        host, path = parent.split(':', 1)
        return host + ':' + posixpath.normpath(path + '/' + url)
    return str((Path(parent) / url).resolve())


def mappings(values):
    result = {}
    for value in values:
        name, separator, target = value.partition('=')
        safe_path(name)
        if not separator or not target or name in result:
            raise CaseError('Mappings must be unique SUBMODULE_PATH=VALUE pairs')
        result[name] = target
    return result


def local_module(source, path, name):
    candidate = source / path
    if (candidate / '.git').exists():
        return candidate.resolve()
    if name:
        safe_path(name)
        common = Path(os.fsdecode(git(source, 'rev-parse', '--git-common-dir')).rstrip('\n'))
        candidate = (source / common / 'modules' / name).resolve()
        if candidate.is_dir():
            return candidate
    return None


def lfs_roots(source):
    common = Path(os.fsdecode(git(source, 'rev-parse', '--git-common-dir')).rstrip('\n'))
    storage = config(source, 'lfs.storage')
    # Git LFS resolves relative lfs.storage against the repository Git directory.
    return [(source / common / (storage or 'lfs') / 'objects').resolve()]


def lfs_object(root, oid):
    return root / oid[:2] / oid[2:4] / oid


def collect_lfs(source, snapshot, start, pointers, output, args):
    if not pointers:
        return
    roots = [Path(p).resolve() for p in getattr(args, 'lfs_object_dir', [])] + lfs_roots(source) + [snapshot / 'lfs/objects']
    remote = origin_url(source)
    for item in pointers:
        oid = item['oid']
        destination = output / 'lfs' / oid
        if destination.exists():
            if destination.stat().st_size != item['size']:
                raise CaseError('Conflicting LFS object sizes')
            continue
        found = next((p for root in roots for p in (lfs_object(root, oid), root / oid)
                      if p.is_file() and not p.is_symlink()), None)
        if found is None and getattr(args, 'fetch_missing', False) and remote:
            # Download into isolated storage, never the source's cache.
            git(snapshot, 'config', 'remote.origin.url', remote)
            for key in ('lfs.url', 'remote.origin.lfsurl'):
                value = config(source, key)
                if value:
                    git(snapshot, 'config', key, value)
            git(snapshot, '-c', 'lfs.fetchinclude=', '-c', 'lfs.fetchexclude=',
                'lfs', 'fetch', 'origin', start)
            found = lfs_object(snapshot / 'lfs' / 'objects', oid)
        if found is None or not found.is_file():
            raise CaseError('Missing LFS object ' + oid + '; provide --lfs-object-dir or use --fetch-missing')
        if found.stat().st_size != item['size'] or file_digest(found) != oid:
            raise CaseError('LFS object content does not match its pointer')
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(found, destination)


def snapshot_repository(source, base, path, output, args, patches, sources, used, stack=()):
    """Bundle each repository bottom-up, updating parent gitlinks for explicit patches."""
    source = Path(source).resolve()
    marker = (str(source), base)
    if marker in stack or len(stack) >= 64:
        raise CaseError('Cyclic or excessively nested submodule graph')
    with tempfile.TemporaryDirectory(prefix='work-to-bench-snapshot-') as tmp:
        snapshot = Path(tmp)
        fmt = git(source, 'rev-parse', '--show-object-format').decode().strip()
        git(snapshot, 'init', '--bare', '--object-format=' + fmt)
        try:
            git(snapshot, 'fetch', '--no-tags', '--', str(source), base)
        except CaseError:
            remote = origin_url(source)
            if not getattr(args, 'fetch_missing', False) or not remote:
                raise CaseError('Missing repository commit; fetch it first or use --fetch-missing')
            git(snapshot, 'fetch', '--no-tags', '--', remote, base)
        git(snapshot, 'read-tree', base)
        if path in patches:
            patch = regular(patches[path])
            if not patch.strip():
                raise CaseError('Patch must not be empty')
            git(snapshot, 'apply', '--cached', '--binary', '--whitespace=nowarn', '-', data=patch)
            used.add(('patch', path))
        tree = git(snapshot, 'write-tree').decode().strip()
        # A temporary commit permits inspection of .gitmodules after a root patch.
        candidate = git(snapshot, 'commit-tree', tree, '-p', base,
                        data=b'Benchmark starting snapshot\n').decode().strip()
        links, _ = inspect_tree(snapshot, candidate)
        configs = module_config(snapshot, candidate)
        children = []
        for child_path, child_base in sorted(links.items()):
            full_path = child_path if path == '.' else path + '/' + child_path
            name, url = configs.get(child_path, (None, None))
            child_source = local_module(source, child_path, name)
            if full_path in sources:
                used.add(('source', full_path))
                child_source = Path(sources[full_path]).resolve()
                if not child_source.is_dir():
                    raise CaseError('--submodule-source must refer to a local repository')
            if child_source is None:
                if not getattr(args, 'fetch_missing', False):
                    raise CaseError('Missing submodule ' + full_path + '; provide --submodule-source or use --fetch-missing')
                remote = local_url(resolve_module_url(url, origin_url(source) or str(source)), source)
                if not remote:
                    raise CaseError('Missing submodule URL for ' + full_path)
                child_source = snapshot / ('download-' + hashlib.sha256(os.fsencode(full_path)).hexdigest())
                child_source.mkdir()
                git(child_source, 'init', '--bare', '--object-format=' + fmt)
                git(child_source, 'config', 'remote.origin.url', remote)
                git(child_source, 'fetch', '--no-tags', '--', remote, child_base)
            child, descendants = snapshot_repository(child_source, child_base, full_path,
                output, args, patches, sources, used, stack + (marker,))
            children.extend([child, *descendants])
            git(snapshot, 'update-index', '--cacheinfo', '160000', child['start_commit'], child_path)
        final_tree = git(snapshot, 'write-tree').decode().strip()
        base_tree = git(snapshot, 'rev-parse', base + '^{tree}').decode().strip()
        start = base if final_tree == base_tree else git(snapshot, 'commit-tree', final_tree,
            '-p', base, data=b'Benchmark starting snapshot\n').decode().strip()
        _, pointers = inspect_tree(snapshot, start)
        git(snapshot, 'update-ref', 'refs/heads/benchmark-start', start)
        git(snapshot, 'symbolic-ref', 'HEAD', 'refs/heads/benchmark-start')
        collect_lfs(source, snapshot, start, pointers, output, args)
        bundle = 'repository.bundle' if path == '.' else 'submodules/' + hashlib.sha256(os.fsencode(path)).hexdigest() + '.bundle'
        (output / bundle).parent.mkdir(parents=True, exist_ok=True)
        ref = 'refs/heads/benchmark-start'
        git(snapshot, 'update-ref', ref, start)
        git(snapshot, 'bundle', 'create', str(output / bundle), ref)
        return {'path': path, 'base_commit': base, 'start_commit': start,
                'object_format': fmt, 'bundle': bundle, 'ref': ref, 'lfs': pointers}, children


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
        patches = mappings(getattr(args, 'submodule_patch', []))
        sources = mappings(getattr(args, 'submodule_source', []))
        if args.patch:
            patches['.'] = args.patch
        used = set()
        repository, modules = snapshot_repository(root, base, '.', output, args, patches, sources, used)
        expected = {('patch', key) for key in patches} | {('source', key) for key in sources}
        if used != expected:
            raise CaseError('A submodule mapping does not match the selected starting tree')
        repository['identity'] = args.source
        (output / 'prompt.md').write_bytes(prompt)
        if context:
            (output / 'context').mkdir()
            for name, data in context.items():
                (output / 'context' / name).write_bytes(data)
        contexts = ['context/' + name for name in sorted(context)]
        files = ['prompt.md', repository['bundle']] + contexts + [m['bundle'] for m in modules]
        files += sorted({'lfs/' + item['oid'] for r in [repository, *modules] for item in r['lfs']})
        metadata = {
            'schema_version': 2, 'id': args.id,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'repository': repository, 'submodules': modules,
            'prompt': 'prompt.md', 'context': contexts,
            'extraction': {'tool': 'work-to-bench', 'patch_applied': bool(patches)},
            'sha256': {name: file_digest(output / name) for name in files},
        }
        (output / 'case.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        validate(output)
    except BaseException:
        shutil.rmtree(output)
        raise
    return metadata


def repository_records(meta):
    return [meta['repository'], *meta.get('submodules', [])]


def read_metadata(directory):
    try:
        meta = json.loads(regular(directory / 'case.json'))
        if type(meta['schema_version']) is not int or meta['schema_version'] not in (1, 2):
            raise CaseError('Unsupported schema version')
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', meta['id']):
            raise CaseError('Invalid case ID')
        timestamp = datetime.fromisoformat(meta['created_at'])
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise CaseError('Extraction timestamp must use UTC')
        identity = meta['repository']['identity']
        if not isinstance(identity, str) or not identity.strip():
            raise CaseError('Missing repository identity')
        if (meta['prompt'] != 'prompt.md'
                or type(meta['extraction']['patch_applied']) is not bool
                or meta['extraction']['tool'] != 'work-to-bench'):
            raise CaseError('Invalid extraction metadata')
        if meta['schema_version'] == 1:
            if 'submodules' in meta or 'lfs' in meta['repository']:
                raise CaseError('External objects require schema version 2')
        elif not isinstance(meta['submodules'], list):
            raise CaseError('Invalid submodules list')
        records = repository_records(meta)
        paths = set()
        files = ['prompt.md']
        for index, record in enumerate(records):
            path = record.get('path', '.') if meta['schema_version'] == 1 else record['path']
            if index == 0:
                if path != '.' or record['bundle'] != 'repository.bundle':
                    raise CaseError('Invalid root repository')
            else:
                safe_path(path)
                expected = 'submodules/' + hashlib.sha256(os.fsencode(path)).hexdigest() + '.bundle'
                if record['bundle'] != expected:
                    raise CaseError('Invalid submodule bundle path')
            if path in paths:
                raise CaseError('Duplicate repository path')
            paths.add(path)
            if record['object_format'] not in ('sha1', 'sha256'):
                raise CaseError('Unsupported Git object format')
            length = 40 if record['object_format'] == 'sha1' else 64
            for key in ('base_commit', 'start_commit'):
                if not re.fullmatch('[0-9a-f]{' + str(length) + '}', record[key]):
                    raise CaseError('Invalid commit ID')
            if record['ref'] != 'refs/heads/benchmark-start':
                raise CaseError('Unexpected Git ref')
            files.append(record['bundle'])
            pointers = record.get('lfs', []) if meta['schema_version'] == 1 else record['lfs']
            if not isinstance(pointers, list):
                raise CaseError('Invalid LFS list')
            for pointer in pointers:
                safe_path(pointer['path'])
                if (not re.fullmatch('[0-9a-f]{64}', pointer['oid'])
                        or type(pointer['size']) is not int or pointer['size'] < 0):
                    raise CaseError('Invalid LFS pointer metadata')
                files.append('lfs/' + pointer['oid'])
        contexts = meta['context']
        if not isinstance(contexts, list) or len(set(contexts)) != len(contexts):
            raise CaseError('Invalid context list')
        for name in contexts:
            safe_path(name)
            if not name.startswith('context/') or len(name.split('/')) != 2:
                raise CaseError('Invalid context path')
        files.extend(contexts)
        if not isinstance(meta['sha256'], dict) or set(meta['sha256']) != set(files):
            raise CaseError('Hash manifest does not match case files')
        for name in set(files):
            if file_digest(case_file(directory, name)) != meta['sha256'][name]:
                raise CaseError('Case file hash mismatch: ' + name)
        text_input(directory / 'prompt.md')
        return meta
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, CaseError):
            raise
        raise CaseError('Invalid case metadata') from exc


def load_bundle(target, directory, record, bare=True):
    git(target, 'init', *(['--bare'] if bare else []), '--object-format=' + record['object_format'])
    git(target, 'bundle', 'verify', str(directory / record['bundle']))
    git(target, 'fetch', '--no-tags', str(directory / record['bundle']), record['ref'])
    start = git(target, 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
    if start != record['start_commit']:
        raise CaseError('Bundle does not contain the specified starting commit')
    git(target, 'merge-base', '--is-ancestor', record['base_commit'], start)
    return start


def validate(directory):
    directory = Path(directory).resolve()
    meta = read_metadata(directory)
    modules = {item['path']: item for item in meta.get('submodules', [])}
    expected_modules = set()
    for record in repository_records(meta):
        with tempfile.TemporaryDirectory(prefix='work-to-bench-verify-') as tmp:
            start = load_bundle(tmp, directory, record)
            if not meta['extraction']['patch_applied'] and start != record['base_commit']:
                raise CaseError('Unpatched case must start at its base commit')
            links, pointers = inspect_tree(tmp, start)
            if pointers != record.get('lfs', []):
                raise CaseError('LFS metadata does not match the starting tree')
            for pointer in pointers:
                content = directory / 'lfs' / pointer['oid']
                if content.stat().st_size != pointer['size'] or meta['sha256']['lfs/' + pointer['oid']] != pointer['oid']:
                    raise CaseError('LFS object does not match its pointer')
            for child, oid in links.items():
                path = child if record.get('path', '.') == '.' else record['path'] + '/' + child
                expected_modules.add(path)
                if path not in modules or modules[path]['start_commit'] != oid:
                    raise CaseError('Submodule metadata does not match the parent gitlink')
    if expected_modules != set(modules):
        raise CaseError('Case contains unrelated submodule bundles')
    return meta


def restore(directory, output):
    directory = Path(directory).resolve()
    output = Path(output).absolute()
    if directory == output.resolve() or directory in output.resolve().parents:
        raise CaseError('Restore output must be outside the case directory')
    if output.exists() or output.is_symlink():
        raise CaseError('Restore output already exists')
    meta = validate(directory)
    if any(record.get('lfs') for record in repository_records(meta)):
        try:
            git(directory, 'lfs', 'version')
        except CaseError as exc:
            raise CaseError('Restoring LFS content requires Git LFS; install it and retry') from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    try:
        for record in sorted(repository_records(meta), key=lambda r: r.get('path', '.').count('/')):
            path = record.get('path', '.')
            target = output if path == '.' else case_file(output, path)
            target.mkdir(parents=True, exist_ok=True)
            if any(target.iterdir()):
                raise CaseError('Submodule restore target is not empty')
            start = load_bundle(target, directory, record, bare=False)
            git(target, '-c', 'submodule.recurse=false', 'checkout', '--detach', start)
            if record.get('lfs'):
                # Populate verified objects, then materialize bytes without network access.
                git(target, 'config', 'filter.lfs.clean', 'git-lfs clean -- %f')
                git(target, 'config', 'filter.lfs.smudge', 'git-lfs smudge -- %f')
                git(target, 'config', 'filter.lfs.process', 'git-lfs filter-process')
                git(target, 'config', 'filter.lfs.required', 'true')
                for item in record['lfs']:
                    content = directory / 'lfs' / item['oid']
                    cached = lfs_object(target / '.git/lfs/objects', item['oid'])
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(content, cached)
                git(target, 'lfs', 'checkout')
                for item in record['lfs']:
                    destination = case_file(target, item['path'])
                    if destination.stat().st_size != item['size'] or file_digest(destination) != item['oid']:
                        raise CaseError('Restored LFS file does not match its pointer')
    except BaseException:
        shutil.rmtree(output)
        raise
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    new = commands.add_parser('create', help='Create and verify a new benchmark case')
    for arg in ('repo', 'base', 'id', 'source', 'prompt', 'output'):
        new.add_argument('--' + arg, required=True)
    new.add_argument('--patch', help='Reviewed Git patch of prerequisite changes relative to base')
    new.add_argument('--context', action='append', default=[], help='Explicit additional input file (repeatable)')
    new.add_argument('--submodule-source', action='append', default=[], metavar='PATH=REPOSITORY',
                     help='Local repository supplying a submodule (repeatable, paths relative to root)')
    new.add_argument('--submodule-patch', action='append', default=[], metavar='PATH=PATCH',
                     help='Explicit prerequisite patch inside a submodule (repeatable)')
    new.add_argument('--lfs-object-dir', action='append', default=[],
                     help='Additional flat or sharded LFS object directory (repeatable)')
    new.add_argument('--fetch-missing', action='store_true',
                     help='Fetch missing submodules and LFS objects into temporary storage')
    restored = commands.add_parser('restore', help='Restore all repositories and LFS files offline')
    restored.add_argument('directory')
    restored.add_argument('--output', required=True)
    check = commands.add_parser('validate', help='Validate case files and independently restore the snapshot')
    check.add_argument('directory')
    args = parser.parse_args()
    try:
        if args.command == 'create':
            meta = create(args)
        elif args.command == 'restore':
            meta = restore(args.directory, args.output)
        else:
            meta = validate(args.directory)
    except (CaseError, OSError) as exc:
        print('error: ' + (str(exc) if isinstance(exc, CaseError) else 'File operation failed'), file=sys.stderr)
        return 1
    print(json.dumps({'id': meta['id'], 'start_commit': meta['repository']['start_commit'], 'valid': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
