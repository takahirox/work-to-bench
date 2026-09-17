#!/usr/bin/env python3
"""Execute a portable benchmark case and retain its results for comparison."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from runner_adapters import ADAPTERS


class RunError(ValueError):
    pass


def case_tools():
    source = Path(__file__).resolve().parents[2] / 'extract-benchmark-case/scripts/benchmark_case.py'
    if not source.is_file():
        raise RunError('Install extract-benchmark-case alongside run-benchmark')
    spec = importlib.util.spec_from_file_location('work_to_bench_case', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def process_environment():
    # Inherited Git routing variables must never point into the caller's repository.
    return {key: value for key, value in os.environ.items()
            if not key.startswith('GIT_') and key not in ('CODEX_THREAD_ID',)}


def pricing_table(path, model):
    if path is None:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if value['model'] != model:
            raise RunError('Pricing model must match the requested model exactly')
        if not isinstance(value['source'], str) or not value['source'].strip():
            raise RunError('Pricing requires a source description or URL')
        datetime.fromisoformat(value['effective_date'])
        for field in ('input_usd_per_million', 'cached_input_usd_per_million', 'output_usd_per_million'):
            rate = Decimal(str(value[field]))
            if not rate.is_finite() or rate < 0:
                raise RunError('Prices must be finite, nonnegative USD amounts')
        return value
    except (OSError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, RunError):
            raise
        raise RunError('Invalid pricing JSON') from exc


def estimate_cost(telemetry, rates, model):
    result = {'reported_usd': None, 'estimated_usd': None, 'pricing': rates,
              'reason': 'pricing_not_supplied'}
    if rates is None:
        return result
    if telemetry.get('reported_model') not in (None, model):
        result['reason'] = 'reported_model_differs_from_pricing'
        return result
    usage = telemetry['usage']['totals']
    needed = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if any(usage.get(key) is None for key in needed):
        result['reason'] = 'usage_incomplete'
        return result
    # Codex input_tokens includes cached input; reasoning is part of output_tokens.
    fresh = usage['input_tokens'] - usage['cached_input_tokens']
    cost = (Decimal(fresh) * Decimal(str(rates['input_usd_per_million']))
            + Decimal(usage['cached_input_tokens']) * Decimal(str(rates['cached_input_usd_per_million']))
            + Decimal(usage['output_tokens']) * Decimal(str(rates['output_usd_per_million']))) / Decimal(1000000)
    result.update(estimated_usd=str(cost), reason='token_rate_estimate_not_a_bill')
    return result


def stop_process(process):
    """Stop the agent and children in its process group, including on timeout."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def collect_artifacts(output, meta, case):
    artifacts, errors = [], []
    workspace = output / 'workspace'
    for record in case.repository_records(meta):
        path = record.get('path', '.')
        label = 'root' if path == '.' else hashlib.sha256(os.fsencode(path)).hexdigest()
        folder = output / 'artifacts' / label
        folder.mkdir(parents=True)
        entry = {'path': path, 'start_commit': record['start_commit'], 'final_head': None,
                 'diff': None, 'status': None, 'untracked': None}
        artifacts.append(entry)
        try:
            repo = workspace if path == '.' else case.case_file(workspace, path)
            if not repo.is_dir():
                raise RunError('Repository was removed during execution')
            entry['final_head'] = case.git(repo, 'rev-parse', 'HEAD').decode().strip()
            env = process_environment()
            env.update(GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
            commands = {
                'diff': ('changes.patch', ['diff', '--no-ext-diff', '--no-textconv', '--binary', record['start_commit'], '--']),
                'status': ('status.txt', ['status', '--porcelain=v1', '--untracked-files=all']),
                'untracked': ('untracked.zlist', ['ls-files', '--others', '--exclude-standard', '-z']),
            }
            for key, (name, arguments) in commands.items():
                destination = folder / name
                with destination.open('wb') as stream:
                    finished = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                        '-c', 'core.fsmonitor=false', '-C', str(repo), *arguments],
                        stdout=stream, stderr=subprocess.PIPE, env=env, timeout=60)
                if finished.returncode:
                    raise RunError('Unable to collect Git ' + key)
                entry[key] = str(destination.relative_to(output))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append({'repository': path, 'message': str(exc) if isinstance(exc, ValueError) else 'Artifact collection failed'})
    return artifacts, errors


def run_benchmark(case_directory, output, *, model, effort=None, agent='codex',
                  executable='codex', timeout=1800, pricing=None, adapter=None):
    """Run once. A custom adapter can implement the three CodexAdapter methods."""
    if os.name != 'posix':
        raise RunError('The process-group runner currently requires macOS or Linux')
    if not model or not isinstance(model, str):
        raise RunError('An explicit model is required')
    if not math.isfinite(timeout) or timeout <= 0:
        raise RunError('Timeout must be a positive finite number of seconds')
    if adapter is None:
        if agent not in ADAPTERS:
            raise RunError('Unknown agent adapter')
        adapter = ADAPTERS[agent]()
    rates = pricing_table(pricing, model)
    case = case_tools()
    case_directory = Path(case_directory).resolve()
    output = Path(output).absolute()
    resolved = output.resolve()
    if resolved == case_directory or case_directory in resolved.parents:
        raise RunError('Run output must be outside the case directory')
    if any((parent / '.git').exists() for parent in [resolved, *resolved.parents]):
        raise RunError('Run output must be outside existing Git working trees')
    if output.exists() or output.is_symlink():
        raise RunError('Run output already exists')
    output = resolved
    try:
        meta = case.validate(case_directory)
    except (ValueError, OSError) as exc:
        raise RunError('Invalid benchmark case: ' + str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    begin = time.monotonic()
    result = {
        'schema_version': 1, 'case_id': meta['id'], 'status': 'preparing',
        'agent': {'adapter': adapter.name, 'provider': adapter.provider, 'version': None,
                  'requested_model': model, 'reported_model': None, 'reasoning_effort': effort},
        'configuration': {'timeout_seconds': timeout, 'sandbox': 'workspace-write',
                          'network_access': False, 'approval_policy': 'never'},
        'starting_repositories': [{'path': r.get('path', '.'), 'commit': r['start_commit']}
                                  for r in case.repository_records(meta)],
        'timing': {'started_at': now(), 'finished_at': None, 'elapsed_seconds': None,
                   'agent_started_at': None, 'agent_finished_at': None, 'agent_elapsed_seconds': None},
        'exit_code': None, 'usage': None, 'cost': None,
        'logs': {'stdout': 'stdout.jsonl', 'stderr': 'stderr.log'},
        'workspace': 'workspace', 'inputs': 'inputs', 'artifacts': [], 'warnings': [], 'errors': [],
    }
    write_json(output / 'result.json', result)
    workspace = output / 'workspace'
    stdout, stderr = output / 'stdout.jsonl', output / 'stderr.log'
    stdout.touch()
    stderr.touch()
    process = None
    restored = False
    telemetry = {'completed': False, 'reported_model': None,
                 'usage': {'totals': {}, 'raw_turns': []}, 'warnings': []}
    agent_start = None
    try:
        inputs = output / 'inputs'
        inputs.mkdir()
        write_json(inputs / 'case.json', meta)
        (inputs / 'prompt.md').write_bytes(case.regular(case_directory / 'prompt.md'))
        for name in meta['context']:
            dest = inputs / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(case.regular(case_directory / name))
        case.restore(case_directory, workspace)
        restored = True
        prompt = ('Execute the benchmark task below in the current repository. '
                  'References to context/ files resolve under ' + str(inputs / 'context') + '. '
                  'Do not alter the input files or other runs. Do not redeem usage resets, buy allowance, '
                  'or switch models/providers if a usage limit is reached; stop and report it.\n\n'
                  + (inputs / 'prompt.md').read_text(encoding='utf-8'))
        (inputs / 'invocation.md').write_text(prompt, encoding='utf-8')
        env = process_environment()
        version = subprocess.run(adapter.version_command(executable), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env, cwd=workspace, timeout=15)
        if version.returncode:
            raise RunError('Unable to determine agent version')
        result['agent']['version'] = version.stdout.strip()[:200]
        command = adapter.command(executable, workspace, model, effort)
        result['command'] = command
        result['status'] = 'running'
        result['timing']['agent_started_at'] = now()
        write_json(output / 'result.json', result)
        agent_start = time.monotonic()
        with (inputs / 'invocation.md').open('rb') as incoming, stdout.open('wb') as out, stderr.open('wb') as err:
            process = subprocess.Popen(command, cwd=workspace, stdin=incoming, stdout=out, stderr=err,
                                       env=env, start_new_session=True)
            try:
                process.wait(timeout=timeout)
                result['status'] = 'completed' if process.returncode == 0 else 'agent_failed'
            except subprocess.TimeoutExpired:
                result['status'] = 'timed_out'
            finally:
                stop_process(process)
                result['exit_code'] = process.returncode
    except KeyboardInterrupt:
        result['status'] = 'interrupted'
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result['status'] = 'runner_failed'
        result['errors'].append(str(exc) if isinstance(exc, ValueError) else 'Agent launch or setup failed; check the executable and environment')
    finally:
        if process is not None:
            stop_process(process)
            result['exit_code'] = process.returncode
        if agent_start is not None:
            result['timing']['agent_finished_at'] = now()
            result['timing']['agent_elapsed_seconds'] = time.monotonic() - agent_start
        try:
            telemetry = adapter.parse(stdout)
            result['agent']['reported_model'] = telemetry.get('reported_model')
            result['usage'] = telemetry['usage']
            result['warnings'].extend(telemetry.get('warnings', []))
            if result['status'] == 'completed' and not telemetry['completed']:
                result['status'] = 'agent_failed'
        except (OSError, ValueError, KeyError, TypeError):
            result['warnings'].append('Unable to parse agent telemetry; raw logs retained')
            if result['status'] == 'completed':
                result['status'] = 'agent_failed'
        result['cost'] = estimate_cost(telemetry, rates, model)
        if restored:
            result['artifacts'], errors = collect_artifacts(output, meta, case)
            result['errors'].extend(errors)
            if errors and result['status'] == 'completed':
                result['status'] = 'artifact_failed'
        result['timing']['finished_at'] = now()
        result['timing']['elapsed_seconds'] = time.monotonic() - begin
        write_json(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_directory')
    parser.add_argument('--output', required=True)
    parser.add_argument('--agent', choices=sorted(ADAPTERS), default='codex')
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', choices=('none', 'minimal', 'low', 'medium', 'high', 'xhigh'))
    parser.add_argument('--executable', default='codex')
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--pricing', help='JSON table of per-million-token USD rates for this model')
    args = parser.parse_args()
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        result = run_benchmark(args.case_directory, args.output, model=args.model, effort=args.effort,
                               agent=args.agent, executable=args.executable, timeout=args.timeout, pricing=args.pricing)
    except (RunError, OSError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({'case_id': result['case_id'], 'status': result['status'],
                      'result': str(Path(args.output).absolute() / 'result.json')}))
    return {'completed': 0, 'timed_out': 124, 'interrupted': 130}.get(result['status'], 1)


if __name__ == '__main__':
    sys.exit(main())
