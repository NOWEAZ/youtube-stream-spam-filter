"""Small, allowlisted local .env loader. Process variables take precedence."""
import os

KEYS = {'OPENCODE_API_KEY', 'OPENCODE_PROVIDER', 'OPENCODE_MODEL', 'YCSF_TOKEN'}


def load_env(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.strip().partition('=')
        if not separator or key not in KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
