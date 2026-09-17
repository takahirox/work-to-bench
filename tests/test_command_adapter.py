import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_benchmark_case as support

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/run-benchmark/scripts'
sys.path.insert(0, str(SCRIPTS))
import benchmark_runner as runner
from command_adapter import CommandAdapter


class CommandTests(unittest.TestCase):
    setUp_base = support.CaseTests.setUp
    state = support.CaseTests.state

    def setUp(self):
        self.setUp_base()
        support.case.create(self.args)
        self.run_dir = self.root / 'run'
        self.script = self.root / 'fake agent.py'
        self.script.write_text('''import json,sys,time,os
from pathlib import Path
if '--version' in sys.argv:
 print('external-test 1.0'); sys.exit(0)
prompt=sys.stdin.read()
Path('received.txt').write_text(prompt)
Path('task.txt').write_text('external agent change\\n')
Path('args.json').write_text(json.dumps(sys.argv[1:]))
print('plain output, not JSON',flush=True)
print('diagnostic',file=sys.stderr,flush=True)
if os.environ.get('COMMAND_MODE')=='timeout': time.sleep(10)
if os.environ.get('COMMAND_MODE')=='fail': sys.exit(7)
''')
        self.config = {'name':'fake-external', 'command':[sys.executable,str(self.script)]}

    def run_case(self, **kwargs):
        return runner.run_benchmark(self.output, self.run_dir, agent='command', agent_config=self.config, **kwargs)

    def test_plain_command_without_model_version_or_codex(self):
        before = self.state()
        result = self.run_case()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['agent']['integration'], 'fake-external')
        self.assertIsNone(result['agent']['version'])
        self.assertIsNone(result['agent']['requested_model'])
        self.assertIsNone(result['agent']['provider'])
        self.assertIsNone(result['usage']['totals']['input_tokens'])
        self.assertIsNone(result['cost']['reported_usd'])
        self.assertEqual(result['logs']['stdout'], 'stdout.log')
        self.assertIn('plain output', (self.run_dir/'stdout.log').read_text())
        self.assertIn('diagnostic', (self.run_dir/'stderr.log').read_text())
        self.assertIn('external agent change', (self.run_dir/result['artifacts'][0]['diff']).read_text())
        self.assertEqual(self.state(), before)
        self.assertEqual(json.loads((self.run_dir/'result.json').read_text()), result)

    def test_file_placeholders_conditions_model_and_version(self):
        self.config.update(command=self.config['command'] + ['{workspace}', '{prompt_file}', '{context_dir}',
            '{conditions_file}', '--model={model}', '--effort={effort}'],
            supported_conditions=['network_access'], version_command=[sys.executable,str(self.script),'--version'])
        model = 'name with spaces; $(touch should-not-exist)'
        result = self.run_case(model=model, effort='custom', conditions={'network_access':True})
        self.assertEqual(result['status'],'completed')
        args = json.loads((self.run_dir/'workspace/args.json').read_text())
        self.assertEqual(args[0], str((self.run_dir/'workspace').resolve()))
        self.assertEqual(Path(args[1]).read_text(), (self.run_dir/'workspace/received.txt').read_text())
        self.assertEqual(json.loads(Path(args[3]).read_text()), {'network_access':True})
        self.assertEqual(args[4], '--model='+model)
        self.assertEqual(result['agent']['version'], 'external-test 1.0')
        self.assertIsNone(result['configuration']['execution_conditions']['effective'])

    def test_reject_unsupported_conditions_and_unused_model(self):
        for kwargs in ({'conditions':{'network_access':True}}, {'model':'ignored'}, {'effort':'ignored'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_case(**kwargs)
            self.assertFalse(self.run_dir.exists())

    def test_invalid_manifest_and_missing_selection(self):
        invalid = [dict(self.config,command='shell command'), dict(self.config, command=[]),
                   dict(self.config,command=['{model}']), dict(self.config, command=['agent','{unknown}']),
                   dict(self.config,supported_conditions=['network_access']), dict(self.config, typo=True)]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                CommandAdapter(config)
        self.config['command'].append('{model}')
        with self.assertRaises(ValueError):
            self.run_case()
        self.assertFalse(self.run_dir.exists())

    def test_failure_and_timeout_preserve_artifacts(self):
        for mode, expected in [('fail','agent_failed'), ('timeout','timed_out')]:
            self.run_dir = self.root/mode
            with patch.dict(os.environ,COMMAND_MODE=mode):
                result = self.run_case(timeout=0.3)
            self.assertEqual(result['status'], expected)
            self.assertTrue((self.run_dir/'workspace/received.txt').is_file())
            self.assertTrue(result['artifacts'])
            if mode == 'fail':
                self.assertEqual(result['exit_code'],7)

    def test_cli_config_and_relative_executable(self):
        # Paths with spaces remain single argv entries; no shell interpretation.
        config = self.root/'agent.json'
        config.write_text(json.dumps(self.config))
        process = subprocess.run([sys.executable,str(SCRIPTS/'benchmark_runner.py'),str(self.output),
            '--output',str(self.run_dir),'--agent','command','--agent-config',str(config)],
            capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(json.loads((self.run_dir/'result.json').read_text())['status'],'completed')
        self.script.write_text('#!/usr/bin/env python3\n'+self.script.read_text())
        self.script.chmod(0o755)
        config.write_text(json.dumps({'name':'relative', 'command':['./fake agent.py']}))
        adapter = CommandAdapter(config)
        self.assertEqual(adapter.config['command'][0], str(self.script.resolve()))

    def test_cli_requires_config_and_codex_still_requires_model(self):
        for args in (['--agent','command'], ['--agent','codex']):
            process = subprocess.run([sys.executable,str(SCRIPTS/'benchmark_runner.py'),str(self.output),
                '--output',str(self.run_dir),*args], capture_output=True,text=True)
            self.assertEqual(process.returncode,1)
            self.assertFalse(self.run_dir.exists())
