import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import test_benchmark_case as support

case = support.case


class ExternalObjectTests(unittest.TestCase):
    setUp = support.CaseTests.setUp
    state = support.CaseTests.state

    def new_repo(self, name):
        repo = self.root / name
        repo.mkdir()
        case.git(repo, 'init')
        (repo / 'input.txt').write_text(name + '\n')
        case.git(repo, 'add', '.')
        case.git(repo, 'commit', '-m', name)
        return repo

    def add_module(self, parent, source, path):
        case.git(parent, '-c', 'protocol.file.allow=always', 'submodule', 'add', str(source), path)
        case.git(parent, 'commit', '-am', 'Add module')
        return parent / path

    def add_lfs(self, repo, data=b'large binary\x00payload', name='asset.bin'):
        oid = hashlib.sha256(data).hexdigest()
        (repo / '.gitattributes').write_text('*.bin filter=lfs diff=lfs merge=lfs -text\n')
        (repo / name).write_text('version https://git-lfs.github.com/spec/v1\noid sha256:' + oid + '\nsize ' + str(len(data)) + '\n')
        common = Path(os.fsdecode(case.git(repo, 'rev-parse', '--git-common-dir')).strip())
        content = case.lfs_object(repo / common / 'lfs/objects', oid)
        content.parent.mkdir(parents=True, exist_ok=True)
        content.write_bytes(data)
        case.git(repo, 'add', '.')
        case.git(repo, 'commit', '-m', 'LFS asset')
        return oid, content

    def manifest(self, meta):
        (self.output / 'case.json').write_text(json.dumps(meta))

    def test_lfs_offline_restore_clean_worktree(self):
        data = b'\x00\xfftest large content\n'
        oid, _ = self.add_lfs(self.repo, data)
        (self.repo / 'asset.bin').chmod(0o755)
        case.git(self.repo, 'add', 'asset.bin')
        case.git(self.repo, 'commit', '-m', 'Executable asset')
        self.args.base = 'HEAD'
        before = self.state()
        meta = case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertEqual(meta['schema_version'], 2)
        self.assertEqual((self.output / 'lfs' / oid).read_bytes(), data)
        self.repo.rename(self.root / 'source-unavailable')
        target = self.root / 'restored'
        case.restore(self.output, target)
        self.assertEqual((target / 'asset.bin').read_bytes(), data)
        self.assertTrue((target / 'asset.bin').stat().st_mode & 0o111)
        self.assertEqual(case.git(target, 'status', '--porcelain'), b'')

    def test_nested_submodules_and_lfs_restore_without_sources(self):
        grand = self.new_repo('grand')
        oid, content = self.add_lfs(grand)
        child = self.new_repo('child')
        nested = self.add_module(child, grand, 'nested')
        child_local = self.add_module(self.repo, child, 'vendor/lib')
        case.git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'update', '--init', '--recursive')
        # LFS cache does not travel with an ordinary Git clone; explicitly supply it.
        self.args.lfs_object_dir = [str(content.parents[2])]
        self.args.base = 'HEAD'
        meta = case.create(self.args)
        self.assertEqual({m['path'] for m in meta['submodules']}, {'vendor/lib', 'vendor/lib/nested'})
        self.repo.rename(self.root / 'source-unavailable')
        child.rename(self.root / 'child-unavailable')
        grand.rename(self.root / 'grand-unavailable')
        target = self.root / 'restored'
        case.restore(self.output, target)
        self.assertEqual((target / 'vendor/lib/nested/asset.bin').read_bytes(), content.read_bytes() if content.exists() else b'large binary\x00payload')
        for repo in (target, target / 'vendor/lib', target / 'vendor/lib/nested'):
            self.assertEqual(case.git(repo, 'status', '--porcelain'), b'')

    def test_explicit_nested_patch_updates_ancestor_gitlinks_only_in_case(self):
        grand = self.new_repo('grand')
        child = self.new_repo('child')
        self.add_module(child, grand, 'nested')
        self.add_module(self.repo, child, 'vendor')
        case.git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'update', '--init', '--recursive')
        nested = self.repo / 'vendor/nested'
        (nested / 'input.txt').write_text('selected prerequisite\n')
        patch = self.root / 'nested.patch'
        patch.write_bytes(case.git(nested, 'diff', '--binary', 'HEAD'))
        case.git(nested, 'add', 'input.txt')
        (nested / 'input.txt').write_text('later solution, excluded\n')
        self.args.base = 'HEAD'
        self.args.submodule_patch = ['vendor/nested=' + str(patch)]
        before = self.state()
        nested_before = case.git(nested, 'diff', '--binary', 'HEAD')
        meta = case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertEqual(nested_before, case.git(nested, 'diff', '--binary', 'HEAD'))
        for record in case.repository_records(meta):
            self.assertNotEqual(record['base_commit'], record['start_commit'])
        target = self.root / 'restored'
        case.restore(self.output, target)
        self.assertEqual((target / 'vendor/nested/input.txt').read_text(), 'selected prerequisite\n')
        self.assertEqual(case.git(target, 'status', '--porcelain'), b'')

    def test_missing_submodule_can_use_local_override(self):
        child = self.new_repo('child')
        self.add_module(self.repo, child, 'vendor')
        case.git(self.repo, 'submodule', 'deinit', '-f', '--all')
        shutil.rmtree(self.repo / '.git/modules')
        self.args.base = 'HEAD'
        with self.assertRaisesRegex(case.CaseError, 'Missing submodule'):
            case.create(self.args)
        self.args.submodule_source = ['vendor=' + str(child)]
        case.create(self.args)
        case.restore(self.output, self.root / 'restored')
        self.assertEqual((self.root / 'restored/vendor/input.txt').read_text(), 'child\n')

    def test_fetch_missing_submodule_is_isolated(self):
        child = self.new_repo('child')
        self.add_module(self.repo, child, 'vendor')
        case.git(self.repo, 'submodule', 'deinit', '-f', '--all')
        shutil.rmtree(self.repo / '.git/modules')
        self.args.base, self.args.fetch_missing = 'HEAD', True
        before = self.state()
        case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertFalse((self.repo / '.git/modules').exists())

    def test_fetch_missing_lfs_from_local_remote_is_isolated(self):
        oid, content = self.add_lfs(self.repo)
        remote = self.root / 'remote'
        case.git(self.root, 'clone', '--bare', str(self.repo), str(remote))
        remote_content = case.lfs_object(remote / 'lfs/objects', oid)
        remote_content.parent.mkdir(parents=True)
        shutil.copyfile(content, remote_content)
        content.unlink()
        case.git(self.repo, 'config', 'remote.origin.url', str(remote))
        self.args.base, self.args.fetch_missing = 'HEAD', True
        before = self.state()
        case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertFalse(content.exists())
        self.assertEqual((self.output / 'lfs' / oid).read_bytes(), remote_content.read_bytes())

    def test_corrupt_lfs_payload_rejected_even_with_updated_manifest(self):
        oid, _ = self.add_lfs(self.repo)
        self.args.base = 'HEAD'
        meta = case.create(self.args)
        (self.output / 'lfs' / oid).write_bytes(b'corrupt')
        meta['sha256']['lfs/' + oid] = case.file_digest(self.output / 'lfs' / oid)
        self.manifest(meta)
        with self.assertRaisesRegex(case.CaseError, 'pointer'):
            case.validate(self.output)

    def test_submodule_metadata_must_match_parent(self):
        child = self.new_repo('child')
        self.add_module(self.repo, child, 'vendor')
        self.args.base = 'HEAD'
        meta = case.create(self.args)
        original = json.loads(json.dumps(meta))
        meta['submodules'] = []
        meta['sha256'].pop(original['submodules'][0]['bundle'])
        self.manifest(meta)
        with self.assertRaisesRegex(case.CaseError, 'gitlink'):
            case.validate(self.output)

    def test_v1_still_validates_and_restores(self):
        meta = case.create(self.args)
        meta['schema_version'] = 1
        del meta['submodules']
        del meta['repository']['path']
        del meta['repository']['lfs']
        self.manifest(meta)
        case.validate(self.output)
        case.restore(self.output, self.root / 'restored')
        self.assertEqual((self.root / 'restored/task.txt').read_text(), 'original\n')

    def test_restore_preserves_existing_output(self):
        case.create(self.args)
        target = self.root / 'restored'
        target.mkdir()
        (target / 'keep').write_text('keep')
        with self.assertRaises(case.CaseError):
            case.restore(self.output, target)
        self.assertEqual((target / 'keep').read_text(), 'keep')

    def test_symlink_payload_directory_is_rejected(self):
        oid, _ = self.add_lfs(self.repo)
        self.args.base = 'HEAD'
        case.create(self.args)
        (self.output / 'lfs').rename(self.root / 'outside-lfs')
        (self.output / 'lfs').symlink_to(self.root / 'outside-lfs')
        with self.assertRaisesRegex(case.CaseError, 'symbolic'):
            case.validate(self.output)

    def test_unknown_patch_mapping_fails_without_source_changes(self):
        patch = self.root / 'unused.patch'
        patch.write_text('unused')
        self.args.submodule_patch = ['missing=' + str(patch)]
        before = self.state()
        with self.assertRaisesRegex(case.CaseError, 'mapping'):
            case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertFalse(self.output.exists())

    def test_relative_submodule_urls(self):
        self.assertEqual(case.resolve_module_url('../child.git', 'https://example.org/org/parent.git'),
                         'https://example.org/org/child.git')
        self.assertEqual(case.resolve_module_url('../child.git', 'git@example.org:org/parent.git'),
                         'git@example.org:org/child.git')
