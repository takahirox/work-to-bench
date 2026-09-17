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

from runner_adapters import ADAPTERS, reject_constant
from execution_conditions import load_conditions
from command_adapter import CommandAdapter


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
        value = json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=reject_constant)
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
    result = {'reported_usd': telemetry.get('reported_cost_usd'), 'estimated_usd': None, 'pricing': rates,
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


TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')


def empty_telemetry():
    return {'completed': False, 'reported_model': None,
            'usage': {'totals': {key: None for key in TOKEN_FIELDS},
                      'missing_fields': list(TOKEN_FIELDS), 'raw_turns': [], 'source': 'unavailable'},
            'warnings': []}


def checked_telemetry(value):
    if type(value['completed']) is not bool or not isinstance(value['usage']['totals'], dict):
        raise ValueError('Invalid adapter telemetry')
    if value.get('reported_model') is not None and not isinstance(value['reported_model'], str):
        raise ValueError('Invalid reported model')
    if not isinstance(value.get('warnings', []), list) or not all(isinstance(w, str) for w in value.get('warnings', [])):
        raise ValueError('Invalid adapter warnings')
    if not isinstance(value['usage']['raw_turns'], list):
        raise ValueError('Invalid raw usage')
    for key in TOKEN_FIELDS:
        count = value['usage']['totals'].get(key)
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError('Invalid normalized token count')
        value['usage']['totals'][key] = count
    totals = value['usage']['totals']
    if totals['input_tokens'] is not None and totals['cached_input_tokens'] is not None:
        if totals['cached_input_tokens'] > totals['input_tokens']:
            raise ValueError('Cached input exceeds total input')
    if value.get('reported_cost_usd') is not None:
        amount = Decimal(str(value['reported_cost_usd']))
        if not amount.is_finite() or amount < 0:
            raise ValueError('Invalid reported charge')
        value['reported_cost_usd'] = str(amount)
    # Reject non-serializable/non-finite provider fields before finalization.
    json.dumps(value, allow_nan=False)
    return value


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


def run_benchmark(case_directory, output, *, model=None, effort=None, agent='codex',
                  executable=None, timeout=1800, pricing=None, adapter=None, conditions=None, agent_config=None):
    """Run once. A custom adapter can implement the three CodexAdapter methods."""
    if os.name != 'posix':
        raise RunError('The process-group runner currently requires macOS or Linux')
    if not math.isfinite(timeout) or timeout <= 0:
        raise RunError('Timeout must be a positive finite number of seconds')
    if agent_config is not None and (adapter is not None or agent != 'command'):
        raise RunError('--agent-config requires --agent command and no Python adapter')
    if adapter is None:
        if agent == 'command':
            if agent_config is None:
                raise RunError('--agent command requires --agent-config')
            if executable is not None:
                raise RunError('Set the executable in the command configuration, not --executable')
            adapter = CommandAdapter(agent_config)
        else:
            if agent not in ADAPTERS:
                raise RunError('Unknown agent adapter')
            adapter = ADAPTERS[agent]()
    if hasattr(adapter, 'validate_selection'):
        adapter.validate_selection(model, effort)
    if pricing is not None and not model:
        raise RunError('Pricing requires an explicit model')
    executable = executable or 'codex'
    requested = load_conditions(conditions)
    if hasattr(adapter, 'configure'):
        adapter.configure(requested)
    elif requested:
        raise RunError('Adapter does not support execution conditions')
    rates = pricing_table(pricing, model)
    if os.sep in str(executable):
        executable = str(Path(executable).resolve())
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
    output.mkdir(mode=0o700)
    begin = time.monotonic()
    stdout_name = getattr(adapter, 'stdout_filename', 'stdout.jsonl')
    if stdout_name not in ('stdout.jsonl', 'stdout.log'):
        raise RunError('Unsupported stdout filename')
    result = {
        'schema_version': 2, 'case_id': meta['id'], 'status': 'preparing',
        'agent': {'adapter': adapter.name, 'provider': adapter.provider, 'version': None,
                  'requested_model': model, 'reported_model': None, 'reasoning_effort': effort,
                  'integration': getattr(adapter, 'integration_name', adapter.name),
                  'integration_config': getattr(adapter, 'config', None)},
        'configuration': {'timeout_seconds': timeout,
                          'execution_conditions': {
                              'requested': requested, 'submitted': None,
                              'effective': None, 'verification': 'unverified',
                              'omitted': 'inherit_agent_environment',
                              'supported': list(getattr(adapter, 'supported_conditions', ())) }},
        'starting_repositories': [{'path': r.get('path', '.'), 'commit': r['start_commit']}
                                  for r in case.repository_records(meta)],
        'timing': {'started_at': now(), 'finished_at': None, 'elapsed_seconds': None,
                   'agent_started_at': None, 'agent_finished_at': None, 'agent_elapsed_seconds': None},
        'exit_code': None, 'usage': None, 'cost': None,
        'logs': {'stdout': stdout_name, 'stderr': 'stderr.log'},
        'workspace': 'workspace', 'inputs': 'inputs', 'artifacts': [], 'warnings': [], 'errors': [],
    }
    write_json(output / 'result.json', result)
    workspace = output / 'workspace'
    stdout, stderr = output / stdout_name, output / 'stderr.log'
    stdout.touch()
    stderr.touch()
    process = None
    restored = False
    telemetry = empty_telemetry()
    agent_start = None
    try:
        inputs = output / 'inputs'
        inputs.mkdir()
        write_json(inputs / 'conditions.json', requested)
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
        version_command = adapter.version_command(executable)
        if version_command is not None:
            version = subprocess.run(version_command, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env, cwd=workspace, timeout=15)
            if version.stderr:
                stderr.write_text(version.stderr, encoding='utf-8')
            if version.returncode:
                raise RunError('Unable to determine agent version')
            result['agent']['version'] = version.stdout.strip()[:200]
        command = adapter.command(executable, workspace, model, effort)
        result['command'] = command
        result['status'] = 'running'
        result['timing']['agent_started_at'] = now()
        write_json(output / 'result.json', result)
        agent_start = time.monotonic()
        with (inputs / 'invocation.md').open('rb') as incoming, stdout.open('wb') as out, stderr.open('ab') as err:
            process = subprocess.Popen(command, cwd=workspace, stdin=incoming, stdout=out, stderr=err,
                                       env=env, start_new_session=True)
            result['configuration']['execution_conditions']['submitted'] = requested.copy()
            write_json(output / 'result.json', result)
            try:
                process.wait(timeout=timeout)
                result['status'] = 'completed' if process.returncode == 0 else 'agent_failed'
            except subprocess.TimeoutExpired:
                result['status'] = 'timed_out'
            finally:
                stop_process(process)
                result['exit_code'] = process.returncode
                process = None
    except KeyboardInterrupt:
        result['status'] = 'interrupted'
    except Exception as exc:
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
            telemetry = checked_telemetry(adapter.parse(stdout))
            result['agent']['reported_model'] = telemetry.get('reported_model')
            result['usage'] = telemetry['usage']
            result['warnings'].extend(telemetry.get('warnings', []))
            if result['status'] == 'completed' and not telemetry['completed']:
                result['status'] = 'agent_failed'
        except Exception:
            result['warnings'].append('Unable to parse agent telemetry; raw logs retained')
            if result['status'] == 'completed':
                result['status'] = 'agent_failed'
        result['usage'] = telemetry['usage']
        try:
            result['cost'] = estimate_cost(telemetry, rates, model)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            result['cost'] = {'reported_usd': None, 'estimated_usd': None, 'pricing': rates,
                              'reason': 'cost_calculation_failed'}
            result['warnings'].append('Unable to calculate cost; usage and pricing retained')
        if restored:
            try:
                result['artifacts'], errors = collect_artifacts(output, meta, case)
            except Exception:
                errors = [{'message': 'Artifact summary collection failed; inspect the preserved workspace'}]
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
    parser.add_argument('--agent', choices=sorted([*ADAPTERS, 'command']), default='codex')
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--executable', help='Codex executable override')
    parser.add_argument('--agent-config', help='External command integration JSON')
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--conditions', help='JSON execution conditions; omitted keys inherit agent settings')
    parser.add_argument('--pricing', help='JSON table of per-million-token USD rates for this model')
    args = parser.parse_args()
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        result = run_benchmark(args.case_directory, args.output, model=args.model, effort=args.effort,
                               agent=args.agent, executable=args.executable, timeout=args.timeout, pricing=args.pricing, conditions=args.conditions, agent_config=args.agent_config)
    except (ValueError, OSError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({'case_id': result['case_id'], 'status': result['status'],
                      'result': str(Path(args.output).absolute() / 'result.json')}))
    return {'completed': 0, 'timed_out': 124, 'interrupted': 130}.get(result['status'], 1)


if __name__ == '__main__':
    sys.exit(main())
