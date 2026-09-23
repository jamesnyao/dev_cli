"""Shared configuration loading for bootstrap and CLI commands."""

import json
import os
from pathlib import Path
import re


def expand_config_path(value):
    """Expand home and environment variables, including $HOME on Windows."""
    value = re.sub(r'\$(?:HOME\b|\{HOME\})', lambda _: str(Path.home()), value)
    return os.path.expandvars(os.path.expanduser(value))


def load_jsonc(path):
    """Load JSON with // and /* */ comments outside quoted strings."""
    content = path.read_text(encoding='utf-8')
    result = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        current = content[index]
        following = content[index + 1] if index + 1 < len(content) else ''
        if in_string:
            result.append(current)
            if escaped:
                escaped = False
            elif current == '\\':
                escaped = True
            elif current == '"':
                in_string = False
        elif current == '"':
            in_string = True
            result.append(current)
        elif current == '/' and following == '/':
            index = content.find('\n', index)
            if index == -1:
                break
            result.append('\n')
        elif current == '/' and following == '*':
            end = content.find('*/', index + 2)
            if end == -1:
                raise json.JSONDecodeError('Unterminated JSONC comment', content, index)
            result.extend('\n' for char in content[index:end + 2] if char == '\n')
            index = end + 1
        else:
            result.append(current)
        index += 1
    return json.loads(''.join(result))


def resolve_override_config_file(script_dir):
    """Resolve the user override without depending on the command dispatcher."""
    override = os.getenv('DEV_CONFIG_OVERRIDE')
    return Path(expand_config_path(override)) if override else script_dir.parent / 'dev_config.json'


def load_config(sample=None, override=None):
    """Merge bundled defaults with the private override; arrays replace wholesale."""
    if sample is None:
        sample = Path(__file__).resolve().parent / 'dev_config.json'
    if override is None:
        override = resolve_override_config_file(sample.parent)
    config = load_jsonc(sample) if sample.is_file() else {}
    if override != sample and override.is_file():
        for key, value in load_jsonc(override).items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    return config
