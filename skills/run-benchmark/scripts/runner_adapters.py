"""Agent-specific invocation and telemetry. No case or process lifecycle logic."""
import json
from pathlib import Path


def reject_constant(value):
    raise ValueError('Non-finite JSON value')


class CodexAdapter:
    name = 'codex'
    provider = 'openai'
    policy = {'sandbox': 'workspace-write', 'network_access': False, 'approval_policy': 'never'}

    def version_command(self, executable):
        return [executable, '--version']

    def command(self, executable, workspace, model, effort):
        # Explicit settings keep benchmark policy independent of personal config.
        settings = {
            'model_provider': 'openai',
            'approval_policy': 'never',
            'sandbox_workspace_write.network_access': False,
            'sandbox_workspace_write.exclude_slash_tmp': True,
            'sandbox_workspace_write.exclude_tmpdir_env_var': True,
            'sandbox_workspace_write.writable_roots': [],
            'features.apps': False,
            'features.hooks': False,
            'features.memories': False,
            'features.multi_agent': False,
            'web_search': 'disabled',
            'projects.' + json.dumps(str(workspace)) + '.trust_level': 'untrusted',
        }
        if effort is not None:
            settings['model_reasoning_effort'] = effort
        command = [executable, 'exec', '--json', '--ephemeral', '--ignore-user-config',
                   '--sandbox', 'workspace-write', '--color', 'never',
                   '--model', model, '--cd', str(workspace)]
        for key, value in settings.items():
            command.extend(['-c', key + '=' + json.dumps(value)])
        return command + ['-']

    def parse(self, stdout):
        turns = []
        warnings = []
        completed = 0
        failed = False
        reported_model = None
        malformed = 0
        active_turn = False
        # Raw logs are retained separately; this summary never guesses absent usage.
        with Path(stdout).open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line, parse_constant=reject_constant)
                    if not isinstance(event, dict):
                        raise ValueError('Expected an event object')
                except ValueError:
                    malformed += 1
                    continue
                kind = event.get('type')
                if kind == 'turn.started':
                    active_turn = True
                if kind == 'thread.started' and isinstance(event.get('model'), str):
                    reported_model = event['model']
                if kind in ('turn.completed', 'turn.failed'):
                    active_turn = False
                    completed += kind == 'turn.completed'
                    failed |= kind == 'turn.failed'
                    usage = event.get('usage')
                    turns.append(usage if isinstance(usage, dict) else {})
                if kind == 'error':
                    # An error event without a terminal success is a failed run.
                    warnings.append('Agent emitted an error event; see stdout.jsonl')
        fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
        totals, missing = {}, []
        for field in fields:
            values = [turn.get(field) for turn in turns]
            if values and all(type(v) is int and v >= 0 for v in values):
                totals[field] = sum(values)
            else:
                totals[field] = None
                missing.append(field)
        if totals['input_tokens'] is not None and totals['cached_input_tokens'] is not None:
            if totals['cached_input_tokens'] > totals['input_tokens']:
                totals['cached_input_tokens'] = None
                missing.append('cached_input_tokens')
                warnings.append('Cached input usage exceeds total input usage')
        if active_turn:
            warnings.append('Final turn did not terminate; usage is partial')
            totals = {field: None for field in fields}
            missing = list(fields)
        if malformed:
            warnings.append(f'{malformed} malformed JSONL events; usage may be incomplete')
            # A lost event could contain another turn, so totals cannot be trusted.
            totals = {field: None for field in fields}
            missing = list(fields)
        if not completed and not failed:
            warnings.append('No terminal turn event was recorded')
        return {
            'completed': completed > 0 and not failed and not malformed and not active_turn,
            'reported_model': reported_model,
            'usage': {'totals': totals, 'missing_fields': missing, 'raw_turns': turns,
                      'source': 'codex-jsonl-turn-events'},
            'warnings': warnings,
        }


ADAPTERS = {'codex': CodexAdapter}
