import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from argparse import Namespace

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/extract-benchmark-case/scripts/benchmark_case.py'
spec = importlib.util.spec_from_file_location('benchmark_case', SCRIPT)
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)


class CaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        case.git(self.repo, 'init')
        (self.repo / 'task.txt').write_text('original\n')
        case.git(self.repo, 'add', '.')
        case.git(self.repo, 'commit', '-m', 'Initial state')
        self.base = case.git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        self.prompt = self.root / 'prompt.md'
        self.prompt.write_text('Add a greeting to task.txt. Preserve its existing content.\n')
        self.output = self.root / 'case'
        self.args = Namespace(repo=str(self.repo), base=self.base, id='greeting',
                              source='example/project', prompt=str(self.prompt),
                              output=str(self.output), patch=None, context=[])

    def state(self):
        return (case.git(self.repo, 'status', '--porcelain=v1', '-uall'),
                case.git(self.repo, 'show-ref'),
                (self.repo / '.git/index').read_bytes(),
                {str(p.relative_to(self.repo)): p.read_bytes()
                 for p in self.repo.rglob('*') if p.is_file() and '.git' not in p.parts})

    def restored(self, meta):
        restored = self.root / 'restored'
        restored.mkdir()
        case.git(restored, 'init')
        case.git(restored, 'fetch', str(self.output / 'repository.bundle'), 'refs/heads/benchmark-start')
        case.git(restored, 'checkout', '--detach', 'FETCH_HEAD')
        self.assertEqual(case.git(restored, 'rev-parse', 'HEAD').decode().strip(),
                         meta['repository']['start_commit'])
        return restored

    def test_existing_commit_preserves_dirty_state_and_excludes_solution(self):
        (self.repo / 'task.txt').write_text('staged solution\n')
        case.git(self.repo, 'add', 'task.txt')
        (self.repo / 'task.txt').write_text('unstaged solution\n')
        (self.repo / 'untracked').write_text('private scratch')
        before = self.state()
        meta = case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertEqual(meta['repository']['start_commit'], self.base)
        restored = self.restored(meta)
        self.assertEqual((restored / 'task.txt').read_text(), 'original\n')
        self.assertFalse((restored / 'untracked').exists())

    def test_patch_binary_deletion_mode_symlink_and_context(self):
        # Prepare explicit prerequisites independently from the source working tree.
        stage = self.root / 'stage'
        case.git(self.root, 'clone', '--no-hardlinks', str(self.repo), str(stage))
        (stage / 'task.txt').unlink()
        (stage / 'binary.dat').write_bytes(bytes(range(256)))
        (stage / 'run.sh').write_text('#!/bin/sh\necho ready\n')
        (stage / 'run.sh').chmod(0o755)
        (stage / 'alias').symlink_to('run.sh')
        case.git(stage, 'add', '.')
        patch = self.root / 'prerequisites.patch'
        patch.write_bytes(case.git(stage, 'diff', '--cached', '--binary', self.base))
        context = self.root / 'requirements.txt'
        context.write_text('Additional requirement\n')
        self.args.patch, self.args.context = str(patch), [str(context)]
        before = self.state()
        meta = case.create(self.args)
        self.assertEqual(before, self.state())
        restored = self.restored(meta)
        self.assertFalse((restored / 'task.txt').exists())
        self.assertEqual((restored / 'binary.dat').read_bytes(), bytes(range(256)))
        self.assertTrue((restored / 'run.sh').stat().st_mode & 0o111)
        self.assertEqual(os.readlink(restored / 'alias'), 'run.sh')
        self.assertEqual((self.output / 'context/requirements.txt').read_bytes(), context.read_bytes())
        self.assertNotEqual(meta['repository']['start_commit'], self.base)

    def test_existing_output_is_not_overwritten(self):
        self.output.mkdir()
        (self.output / 'keep').write_text('keep')
        with self.assertRaises(case.CaseError):
            case.create(self.args)
        self.assertEqual((self.output / 'keep').read_text(), 'keep')

    def test_failed_patch_cleans_output_and_preserves_source(self):
        patch = self.root / 'bad.patch'
        patch.write_text('not a patch')
        self.args.patch = str(patch)
        before = self.state()
        with self.assertRaises(case.CaseError):
            case.create(self.args)
        self.assertEqual(before, self.state())
        self.assertFalse(self.output.exists())

    def test_output_inside_source_is_rejected(self):
        self.args.output = str(self.repo / 'case')
        with self.assertRaises(case.CaseError):
            case.create(self.args)
        self.assertFalse((self.repo / 'case').exists())

    def test_prompt_tampering_is_detected(self):
        case.create(self.args)
        (self.output / 'prompt.md').write_text('changed')
        with self.assertRaisesRegex(case.CaseError, 'hash mismatch'):
            case.validate(self.output)

    def test_metadata_traversal_is_rejected(self):
        meta = case.create(self.args)
        meta['context'] = ['context/../outside']
        (self.output / 'case.json').write_text(json.dumps(meta))
        with self.assertRaises(case.CaseError):
            case.validate(self.output)

    def test_wrong_start_commit_is_detected(self):
        meta = case.create(self.args)
        meta['repository']['start_commit'] = '0' * 40
        (self.output / 'case.json').write_text(json.dumps(meta))
        with self.assertRaisesRegex(case.CaseError, 'starting commit'):
            case.validate(self.output)

    def test_context_symlink_is_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.prompt)
        self.args.context = [str(link)]
        with self.assertRaises(case.CaseError):
            case.create(self.args)

    def test_missing_submodule_preserves_source(self):
        case.git(self.repo, 'update-index', '--add', '--cacheinfo', '160000,' + self.base + ',vendor')
        case.git(self.repo, 'commit', '-m', 'Submodule')
        self.args.base = 'HEAD'
        before = self.state()
        with self.assertRaisesRegex(case.CaseError, 'Missing submodule'):
            case.create(self.args)
        self.assertEqual(before, self.state())

    def test_missing_lfs_object_is_rejected(self):
        (self.repo / 'large').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:' + '0' * 64 + '\nsize 42\n')
        case.git(self.repo, 'add', '.')
        case.git(self.repo, 'commit', '-m', 'LFS pointer')
        self.args.base = 'HEAD'
        with self.assertRaisesRegex(case.CaseError, 'LFS'):
            case.create(self.args)

    def test_cli_create_and_validate(self):
        result = subprocess.run(['python3', str(SCRIPT), 'create', '--repo', str(self.repo),
                                 '--base', self.base, '--id', 'cli-case', '--source', 'example/project',
                                 '--prompt', str(self.prompt), '--output', str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue(json.loads(result.stdout)['valid'])
        result = subprocess.run(['python3', str(SCRIPT), 'validate', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sha256_repository(self):
        repo = self.root / 'sha256-source'
        repo.mkdir()
        case.git(repo, 'init', '--object-format=sha256')
        (repo / 'input').write_text('sha256 input')
        case.git(repo, 'add', '.')
        case.git(repo, 'commit', '-m', 'SHA-256 base')
        self.args.repo, self.args.base = str(repo), 'HEAD'
        meta = case.create(self.args)
        self.assertEqual(meta['repository']['object_format'], 'sha256')
        self.assertEqual(len(meta['repository']['start_commit']), 64)
        self.assertEqual(case.validate(self.output), meta)

    def test_linked_worktree_preserves_index_and_branch(self):
        linked = self.root / 'linked'
        case.git(self.repo, 'worktree', 'add', '-b', 'linked-task', str(linked), self.base)
        (linked / 'task.txt').write_text('worktree staged solution')
        case.git(linked, 'add', '.')
        before = case.git(linked, 'status', '--porcelain=v1')
        index = Path(os.fsdecode(case.git(linked, 'rev-parse', '--git-path', 'index')).strip())
        index_before = index.read_bytes()
        self.args.repo = str(linked)
        meta = case.create(self.args)
        self.assertEqual(case.git(linked, 'status', '--porcelain=v1'), before)
        self.assertEqual(index.read_bytes(), index_before)
        self.assertEqual(case.git(linked, 'branch', '--show-current').strip(), b'linked-task')
        self.assertEqual(meta['repository']['start_commit'], self.base)

    def test_duplicate_context_filenames_are_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        (other / self.prompt.name).write_text('other input')
        self.args.context = [str(self.prompt), str(other / self.prompt.name)]
        with self.assertRaisesRegex(case.CaseError, 'unique'):
            case.create(self.args)

    def test_invalid_metadata_and_bundle_are_rejected(self):
        meta = case.create(self.args)
        original = json.dumps(meta)
        for invalid in ([], {'schema_version': True}, dict(meta, context=[{}]),
                        dict(meta, created_at='2026-01-01T00:00:00')):
            with self.subTest(metadata=invalid):
                (self.output / 'case.json').write_text(json.dumps(invalid))
                with self.assertRaises(case.CaseError):
                    case.validate(self.output)
        (self.output / 'repository.bundle').write_bytes(b'corrupt bundle')
        meta = json.loads(original)
        meta['sha256']['repository.bundle'] = case.file_digest(self.output / 'repository.bundle')
        (self.output / 'case.json').write_text(json.dumps(meta))
        with self.assertRaises(case.CaseError):
            case.validate(self.output)


if __name__ == '__main__':
    unittest.main()
