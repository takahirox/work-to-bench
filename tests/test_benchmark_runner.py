import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import test_benchmark_case as support
import test_external_objects as external

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/run-benchmark/scripts'
sys.path.insert(0, str(SCRIPTS))
import benchmark_runner as runner
from runner_adapters import CodexAdapter


class RunnerTests(unittest.TestCase):
    setUp_base = support.CaseTests.setUp
    state = support.CaseTests.state

    def setUp(self):
        self.setUp_base()
        support.case.create(self.args)
        self.run_dir = self.root / 'run'
        self.executable = self.root / 'fake-codex'
        self.executable.write_text('''#!/usr/bin/env python3
import json,os,sys,time,subprocess
from pathlib import Path
if '--version' in sys.argv:
 print('codex-test 1.0'); sys.exit(0)
assert sys.argv[sys.argv.index('--sandbox')+1] == 'workspace-write'
assert '--ignore-user-config' in sys.argv
workspace=Path(sys.argv[sys.argv.index('--cd')+1])
assert Path.cwd() == workspace
prompt=sys.stdin.read()
(workspace/'received.txt').write_text(prompt)
(workspace/'task.txt').write_text('agent change\\n')
(workspace/'new.bin').write_bytes(b'\\x00\\xffartifact')
mode=os.environ.get('BENCH_TEST_MODE','success')
print(json.dumps({'type':'thread.started','model':'test-model'}),flush=True)
if mode=='timeout':
 subprocess.Popen([sys.executable,'-c',"import time;from pathlib import Path;time.sleep(2);Path('escaped.txt').write_text('bad')"])
 time.sleep(10)
if mode=='external':
 (workspace/'vendor/input.txt').write_text('child change\\n')
 (workspace/'asset.bin').write_bytes(b'changed LFS payload')
if mode=='commit':
 subprocess.run(['git','add','task.txt'],check=True)
 subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Agent commit'],check=True,stdout=sys.stderr)
if mode=='fail':
 print(json.dumps({'type':'turn.failed','error':{'code':'usage_limit_reached','message':'limit reached'}}),flush=True)
 sys.exit(1)
if mode=='missing': sys.exit(0)
if mode=='malformed': print('not JSON')
usage={'input_tokens':100,'cached_input_tokens':40,'output_tokens':10,'reasoning_output_tokens':3}
print(json.dumps({'type':'turn.completed','usage':usage}),flush=True)
''')
        self.executable.chmod(0o755)

    def run_case(self, **kwargs):
        return runner.run_benchmark(self.output, self.run_dir, model='test-model', effort='medium',
                                    executable=str(self.executable), **kwargs)

    def test_success_preserves_source_and_all_artifacts(self):
        before = self.state()
        result = self.run_case()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['totals']['input_tokens'], 100)
        self.assertEqual(result['agent']['version'], 'codex-test 1.0')
        self.assertEqual(result['agent']['reported_model'], 'test-model')
        self.assertEqual((self.run_dir / 'workspace/new.bin').read_bytes(), b'\x00\xffartifact')
        self.assertIn(b'+agent change', (self.run_dir / result['artifacts'][0]['diff']).read_bytes())
        self.assertIn(b'new.bin\0', (self.run_dir / result['artifacts'][0]['untracked']).read_bytes())
        self.assertEqual(result['artifacts'][0]['final_head'], self.base)
        self.assertEqual(self.state(), before)
        self.assertIsNone(result['cost']['estimated_usd'])
        self.assertGreater(result['timing']['agent_elapsed_seconds'], 0)
        self.assertGreaterEqual(result['timing']['elapsed_seconds'], result['timing']['agent_elapsed_seconds'])
        self.assertEqual(json.loads((self.run_dir / 'result.json').read_text()), result)

    def test_multiple_runs_coexist(self):
        first = self.run_case()
        first_path = self.run_dir
        self.run_dir = self.root / 'second'
        second = self.run_case()
        self.assertEqual(first['case_id'], second['case_id'])
        self.assertTrue((first_path / 'workspace/new.bin').is_file())
        self.assertTrue((self.run_dir / 'workspace/new.bin').is_file())

    def test_failure_preserves_logs_and_changes_without_retry(self):
        with patch.dict(os.environ, BENCH_TEST_MODE='fail'):
            result = self.run_case()
        self.assertEqual(result['status'], 'agent_failed')
        self.assertEqual(result['exit_code'], 1)
        self.assertIn('usage_limit_reached', (self.run_dir / 'stdout.jsonl').read_text())
        self.assertTrue((self.run_dir / 'workspace/new.bin').is_file())
        self.assertEqual(len(result['usage']['raw_turns']), 1)
        self.assertIsNone(result['usage']['totals']['input_tokens'])

    def test_timeout_stops_descendants_and_keeps_partial_work(self):
        with patch.dict(os.environ, BENCH_TEST_MODE='timeout'):
            result = self.run_case(timeout=0.25)
        self.assertEqual(result['status'], 'timed_out')
        self.assertTrue((self.run_dir / 'workspace/new.bin').exists())
        time.sleep(2.1)
        self.assertFalse((self.run_dir / 'workspace/escaped.txt').exists())

    def test_missing_terminal_event_is_not_success(self):
        with patch.dict(os.environ, BENCH_TEST_MODE='missing'):
            result = self.run_case()
        self.assertEqual(result['status'], 'agent_failed')
        self.assertTrue(result['warnings'])

    def test_malformed_event_does_not_fabricate_totals(self):
        with patch.dict(os.environ, BENCH_TEST_MODE='malformed'):
            result = self.run_case()
        self.assertEqual(result['status'], 'agent_failed')
        self.assertIsNone(result['usage']['totals']['input_tokens'])

    def test_agent_commits_remain_inspectable(self):
        with patch.dict(os.environ, BENCH_TEST_MODE='commit'):
            result = self.run_case()
        self.assertEqual(result['status'], 'completed')
        self.assertNotEqual(result['artifacts'][0]['final_head'], self.base)
        self.assertIn(b'+agent change', (self.run_dir / result['artifacts'][0]['diff']).read_bytes())

    def test_existing_output_and_outputs_in_source_are_rejected(self):
        self.run_dir.mkdir()
        (self.run_dir / 'keep').write_text('keep')
        with self.assertRaises(runner.RunError):
            self.run_case()
        self.assertEqual((self.run_dir / 'keep').read_text(), 'keep')
        self.run_dir = self.repo / 'run'
        with self.assertRaises(runner.RunError):
            self.run_case()
        self.assertFalse(self.run_dir.exists())

    def test_invalid_case_does_not_launch(self):
        (self.output / 'prompt.md').write_text('tampered')
        with self.assertRaises(runner.RunError):
            self.run_case()
        self.assertFalse(self.run_dir.exists())

    def test_missing_executable_produces_failed_result(self):
        self.executable.unlink()
        result = self.run_case()
        self.assertEqual(result['status'], 'runner_failed')
        self.assertTrue((self.run_dir / 'result.json').exists())
        self.assertTrue(result['artifacts'])

    def test_price_estimate_separates_cached_input(self):
        rates = self.root / 'rates.json'
        rates.write_text(json.dumps({'model': 'test-model', 'source': 'synthetic test rates',
            'effective_date': '2026-01-01', 'input_usd_per_million': 2,
            'cached_input_usd_per_million': 1, 'output_usd_per_million': 10}))
        result = self.run_case(pricing=rates)
        self.assertEqual(result['cost']['estimated_usd'], '0.00026')
        self.assertIsNone(result['cost']['reported_usd'])
        self.assertEqual(result['cost']['pricing']['source'], 'synthetic test rates')

    def test_context_is_delivered_outside_starting_repository(self):
        context = self.root / 'requirements.txt'
        context.write_text('specific requirement')
        self.args.context = [str(context)]
        self.args.output = str(self.root / 'with-context')
        self.output = Path(self.args.output)
        support.case.create(self.args)
        self.run_case()
        self.assertEqual((self.run_dir / 'inputs/context/requirements.txt').read_text(), 'specific requirement')
        self.assertIn(str((self.run_dir / 'inputs/context').resolve()), (self.run_dir / 'workspace/received.txt').read_text())
        self.assertFalse((self.run_dir / 'workspace/context').exists())

    def test_cli_and_exit_status(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'benchmark_runner.py'), str(self.output),
            '--output', str(self.run_dir), '--model', 'test-model', '--executable', str(self.executable)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'completed')

    def test_usage_across_turns_preserves_unknown_categories(self):
        events = self.root / 'events.jsonl'
        events.write_text('\n'.join(json.dumps({'type': 'turn.completed', 'usage': usage}) for usage in (
            {'input_tokens': 10, 'cached_input_tokens': 5, 'output_tokens': 2, 'new_provider_category': 8},
            {'input_tokens': 20, 'cached_input_tokens': 7, 'output_tokens': 4})))
        parsed = CodexAdapter().parse(events)
        self.assertEqual(parsed['usage']['totals']['input_tokens'], 30)
        self.assertIsNone(parsed['usage']['totals']['reasoning_output_tokens'])
        self.assertEqual(parsed['usage']['raw_turns'][0]['new_provider_category'], 8)


    def test_submodule_and_lfs_results_are_preserved(self):
        child = external.ExternalObjectTests.new_repo(self, 'child')
        external.ExternalObjectTests.add_module(self, self.repo, child, 'vendor')
        external.ExternalObjectTests.add_lfs(self, self.repo)
        self.args.base = 'HEAD'
        self.args.output = str(self.root / 'external-case')
        self.output = Path(self.args.output)
        support.case.create(self.args)
        before = self.state()
        with patch.dict(os.environ, BENCH_TEST_MODE='external'):
            result = self.run_case()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual({r['path'] for r in result['artifacts']}, {'.', 'vendor'})
        child_result = next(r for r in result['artifacts'] if r['path'] == 'vendor')
        self.assertIn(b'+child change', (self.run_dir / child_result['diff']).read_bytes())
        self.assertEqual((self.run_dir / 'workspace/asset.bin').read_bytes(), b'changed LFS payload')
        self.assertEqual(self.state(), before)

    def test_sigterm_keeps_partial_result(self):
        env = dict(os.environ, BENCH_TEST_MODE='timeout')
        process = subprocess.Popen([sys.executable, str(SCRIPTS / 'benchmark_runner.py'), str(self.output),
            '--output', str(self.run_dir), '--model', 'test-model', '--executable', str(self.executable),
            '--timeout', '30'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.run_dir / 'workspace/new.bin').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail('Runner exited before the interrupt fixture became ready')
                time.sleep(0.05)
            self.assertTrue((self.run_dir / 'workspace/new.bin').exists())
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertEqual(json.loads(stdout)['status'], 'interrupted')
            saved = json.loads((self.run_dir / 'result.json').read_text())
            self.assertEqual(saved['status'], 'interrupted')
            self.assertTrue(saved['artifacts'])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
