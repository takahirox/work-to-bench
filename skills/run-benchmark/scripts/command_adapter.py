"""External command integration: argv + stdin, without a Codex dependency."""
from pathlib import Path
import re

from execution_conditions import load_conditions

TOKENS = re.compile(r'\{([a-z_]+)\}')
PLACEHOLDERS = {'workspace', 'prompt_file', 'context_dir', 'conditions_file', 'model', 'effort'}


class CommandAdapter:
    name = 'command'
    provider = None
    stdout_filename = 'stdout.log'

    def __init__(self, config):
        base = Path(config).resolve().parent if isinstance(config, (str, Path)) else Path.cwd()
        self.config = load_conditions(config)
        allowed = {'name', 'provider', 'command', 'version_command', 'supported_conditions'}
        if set(self.config) - allowed:
            raise ValueError('Unknown command adapter configuration key')
        if not isinstance(self.config.get('name'), str) or not self.config['name'].strip():
            raise ValueError('Command adapter requires a nonempty name')
        self.integration_name = self.config['name']
        self.provider = self.config.get('provider')
        if self.provider is not None and (not isinstance(self.provider, str) or not self.provider.strip()):
            raise ValueError('Command provider must be a nonempty string or null')
        self.tokens = set()
        for key in ('command', 'version_command'):
            argv = self.config.get(key)
            if key == 'version_command' and argv is None:
                continue
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and '\x00' not in a for a in argv) or not argv[0]:
                raise ValueError(key + ' must be a nonempty argv array without NUL bytes')
            tokens = {m.group(1) for a in argv for m in TOKENS.finditer(a)}
            if tokens - PLACEHOLDERS or (key == 'version_command' and tokens):
                raise ValueError('Unsupported placeholder in ' + key)
            if TOKENS.search(argv[0]):
                raise ValueError('Executable cannot contain placeholders')
            if '/' in argv[0] and not Path(argv[0]).is_absolute():
                argv[0] = str((base / argv[0]).resolve())
            if key == 'command':
                self.tokens = tokens
        supported = self.config.get('supported_conditions', [])
        if not isinstance(supported, list) or not all(isinstance(k, str) and k for k in supported) or len(set(supported)) != len(supported):
            raise ValueError('supported_conditions must contain distinct nonempty keys')
        if supported and 'conditions_file' not in self.tokens:
            raise ValueError('Declared condition support requires {conditions_file} in command')
        self.supported_conditions = supported
        self.conditions = {}

    def configure(self, conditions):
        unsupported = set(conditions) - set(self.supported_conditions)
        if unsupported:
            raise ValueError('Unsupported command execution conditions: ' + ', '.join(sorted(unsupported)))
        self.conditions = conditions

    def validate_selection(self, model, effort):
        for key, value in (('model', model), ('effort', effort)):
            if value is not None and (not isinstance(value, str) or not value or '\x00' in value):
                raise ValueError(key + ' must be a nonempty string')
            if (value is not None) != (key in self.tokens):
                raise ValueError(key + ' must be supplied exactly when its placeholder is used')

    def version_command(self, executable):
        return self.config.get('version_command')

    def command(self, executable, workspace, model, effort):
        inputs = workspace.parent / 'inputs'
        values = {'workspace': str(workspace), 'prompt_file': str(inputs / 'invocation.md'),
                  'context_dir': str(inputs / 'context'), 'conditions_file': str(inputs / 'conditions.json'),
                  'model': model, 'effort': effort}
        return [TOKENS.sub(lambda m: values[m.group(1)], arg) for arg in self.config['command']]

    def parse(self, stdout):
        # The Runner checks process exit status separately. Plain text is valid.
        fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
        return {'completed': True, 'reported_model': None,
                'usage': {'totals': dict.fromkeys(fields), 'missing_fields': list(fields),
                          'raw_turns': [], 'source': 'unavailable'},
                'warnings': ['Command completion uses exit status; structured usage is unavailable.']}
