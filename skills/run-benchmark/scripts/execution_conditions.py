"""Validate execution requests without inventing agent policy defaults."""
import json
from pathlib import Path


def reject_constant(value):
    raise ValueError('Non-finite JSON value')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key: ' + key)
        result[key] = value
    return result


def load_conditions(value):
    if value is None:
        return {}
    if isinstance(value, (str, Path)):
        value = json.loads(Path(value).read_text(encoding='utf-8'),
                           parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError('Execution conditions must be a JSON object')
    return json.loads(json.dumps(value, allow_nan=False))


CHOICES = {
    'sandbox': ('read-only', 'workspace-write', 'danger-full-access'),
    'approval_policy': ('never', 'on-request'),
    'approvals_reviewer': ('user', 'auto_review'),
    'web_search': ('disabled', 'cached', 'live'),
    'project_trust': ('trusted', 'untrusted'),
}
BOOLS = ('network_access', 'exclude_slash_tmp', 'exclude_tmpdir_env_var',
         'ignore_user_config', 'ephemeral')
FEATURES = ('apps', 'hooks', 'memories', 'multi_agent')
SUPPORTED = (*CHOICES, *BOOLS, 'writable_roots', 'features')


def codex_conditions(value):
    conditions = load_conditions(value)
    for key, value in conditions.items():
        if key not in SUPPORTED:
            raise ValueError('Unsupported Codex execution condition: ' + key)
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError('Invalid execution condition: ' + key)
        if key in BOOLS and type(value) is not bool:
            raise ValueError('Execution condition must be boolean: ' + key)
        if key == 'writable_roots':
            if not isinstance(value, list) or not all(isinstance(p, str) and '\x00' not in p and Path(p).is_absolute() for p in value):
                raise ValueError('writable_roots must be a list of absolute paths')
        if key == 'features':
            if not isinstance(value, dict) or any(k not in FEATURES or type(v) is not bool for k, v in value.items()):
                raise ValueError('Unsupported feature or non-boolean feature value')
    workspace_keys = ('network_access', 'writable_roots', 'exclude_slash_tmp', 'exclude_tmpdir_env_var')
    if any(key in conditions for key in workspace_keys) and conditions.get('sandbox') != 'workspace-write':
        raise ValueError('Workspace conditions require explicit sandbox=workspace-write')
    return conditions
