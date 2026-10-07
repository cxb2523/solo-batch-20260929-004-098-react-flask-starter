"""Configuration audit contract — the single implementation.

Both entry points MUST call this module and nothing else:
  - CLI:  ``python -m src.entry audit [--json]`` (see ``src/entry.py``)
  - HTTP: ``GET /api/system/audit``            (see ``src/app.py``)

For every tracked config key the report carries the effective value, the
source layer it came from (default / environment variable / compose
environment / compose env_file), the current process role and the alembic
version state.  Values are always resolved through the existing chain
(``src.settings.Settings`` reads defaults + ``os.environ``; compose only
injects variables into the process environment).  The compose file is
parsed solely to *attribute* the source layer — never to read values —
so no third resolution path is created.
"""

import ast
import os
from collections import namedtuple
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
MIGRATIONS_DIR = BACKEND_DIR / 'migrations'

# Source layers
SOURCE_DEFAULT = 'default'
SOURCE_ENV = 'environment variable'
SOURCE_COMPOSE_ENV = 'compose environment'
SOURCE_COMPOSE_ENV_FILE = 'compose env_file'

# Process roles (compose injects APP_ROLE for the containerised services)
ROLE_TO_SERVICE = {
    'web': 'backend',
    'worker': 'worker',
}

# Explanations surfaced by both the CLI and the /system panel about why a
# local direct run and a container run can legitimately show different
# effective values.
NOTES = [
    'Keys sourced from "compose environment" / "compose env_file" are injected '
    'by docker compose only; a local direct run falls back to the default or '
    'a locally exported variable, so values may differ.',
    'REDIS_HOST defaults to "redis", the compose-internal service name, which '
    'only resolves inside the compose network.',
    'DATABASE_URL is injected by docker-compose.yml (service "environment:"); '
    'it is null in a local run unless exported manually.',
    'APP_ROLE is set by compose (web/worker); the CLI reports role "cli".',
]


class ConfigKey(namedtuple('ConfigKey', ['name', 'env', 'default', 'parser', 'settings_attr'])):
    """One tracked config key.

    name:          report key (matches the Settings attribute where one exists)
    env:           environment variable backing it (None for derived keys)
    default:       default used when the variable is absent ('{KEY}' templates
                   are rendered with already-resolved values)
    parser:        'str' | 'bool' — mirrors how src.settings parses the value
    settings_attr: attribute on src.settings.Settings to cross-check against
                   (None for env-only keys the framework reads directly)
    """
    __slots__ = ()


CONFIG_SPEC = [
    ConfigKey('DEV', 'DEV', 'False', 'bool', 'DEV'),
    ConfigKey('REDIS_HOST', 'REDIS_HOST', 'redis', 'str', 'REDIS_HOST'),
    ConfigKey('REDIS_URL', None, 'redis://{REDIS_HOST}:6379', 'str', 'REDIS_URL'),
    ConfigKey('AES_SECRET_KEY', 'AES_SECRET_KEY', 'fake-aes-key', 'str', 'AES_SECRET_KEY'),
    ConfigKey('SQLALCHEMY_DATABASE_URI', 'DATABASE_URL', None, 'str', 'SQLALCHEMY_DATABASE_URI'),
    ConfigKey('CELERY_BROKER_URL', None, 'redis://{REDIS_HOST}:6379', 'str', 'CELERY_BROKER_URL'),
    ConfigKey('CELERY_RESULT_BACKEND', None, 'redis://{REDIS_HOST}:6379', 'str', 'CELERY_RESULT_BACKEND'),
    ConfigKey('FLASK_DEBUG', 'FLASK_DEBUG', None, 'str', None),
    ConfigKey('LOCALSTACK_HOST', 'LOCALSTACK_HOST', None, 'str', None),
]


def _parse(parser, raw):
    if raw is None:
        return None
    if parser == 'bool':
        return str(raw).lower() in ['true', '1']
    return raw


def _settings():
    from src.settings import Settings
    return Settings


# ---------------------------------------------------------------------------
# Compose file parsing (attribution only — values never come from here)
# ---------------------------------------------------------------------------

def find_compose_file(env=None):
    """Locate docker-compose.yml (env override, cwd, then repo root)."""
    env = os.environ if env is None else env
    candidates = []
    if env.get('AUDIT_COMPOSE_FILE'):
        candidates.append(Path(env['AUDIT_COMPOSE_FILE']))
    candidates.append(Path.cwd() / 'docker-compose.yml')
    candidates.append(REPO_ROOT / 'docker-compose.yml')
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_env_file(path):
    keys = set()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return keys
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        keys.add(line.split('=', 1)[0].strip())
    return keys


def parse_compose_services(compose_path):
    """Return ``{service: {'environment': set(), 'env_file': set()}}``.

    Small indentation-based parser covering the compose subset this project
    uses (PyYAML is intentionally not a backend dependency).  ``env_file``
    entries are resolved relative to the compose file and parsed for keys.
    """
    services = {}
    if not compose_path or not Path(compose_path).is_file():
        return services
    compose_dir = Path(compose_path).parent
    in_services = False
    service = None
    section = None
    section_indent = 0
    for raw in Path(compose_path).read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            in_services = stripped.rstrip(':') == 'services'
            service = None
            section = None
            continue
        if not in_services:
            continue
        if indent == 2 and stripped.endswith(':') and not stripped.startswith('-'):
            service = stripped[:-1].strip("'\"")
            services.setdefault(service, {'environment': set(), 'env_file': set()})
            section = None
            continue
        if service is None:
            continue
        if section is not None and indent <= section_indent and not stripped.startswith('-'):
            section = None
        if indent == 4 and not stripped.startswith('-') and \
                stripped.rstrip(':') in ('environment', 'env_file'):
            section = stripped.rstrip(':')
            section_indent = indent
            continue
        if section is None:
            continue
        if section == 'environment':
            item = stripped[1:].strip() if stripped.startswith('-') else stripped
            if '=' in item:
                services[service]['environment'].add(item.split('=', 1)[0].strip())
            elif ':' in item:
                services[service]['environment'].add(item.split(':', 1)[0].strip())
        elif section == 'env_file' and stripped.startswith('-'):
            env_file = compose_dir / stripped[1:].strip().strip("'\"")
            services[service]['env_file'] |= _parse_env_file(env_file)
    return services


def compose_env_map(compose_path, role):
    """Map env var name -> compose source layer for the current role.

    ``environment:`` overrides ``env_file:`` (compose semantics), so it wins
    attribution when a key appears in both.
    """
    services = parse_compose_services(compose_path)
    if role in ROLE_TO_SERVICE:
        names = [ROLE_TO_SERVICE[role]]
    else:
        names = sorted(set(ROLE_TO_SERVICE.values()))
    result = {}
    for name in names:
        svc = services.get(name, {'environment': set(), 'env_file': set()})
        for key in svc['env_file']:
            result.setdefault(key, SOURCE_COMPOSE_ENV_FILE)
        for key in svc['environment']:
            result[key] = SOURCE_COMPOSE_ENV
    return result


# ---------------------------------------------------------------------------
# Alembic state
# ---------------------------------------------------------------------------

def head_revision(migrations_dir=None):
    """Head revision parsed from the migration scripts (no DB needed)."""
    versions_dir = Path(migrations_dir or MIGRATIONS_DIR) / 'versions'
    revisions, down_revisions = set(), set()
    if not versions_dir.is_dir():
        return None
    for script in versions_dir.glob('*.py'):
        tree = ast.parse(script.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                value = node.value.value if isinstance(node.value, ast.Constant) else None
                if name == 'revision' and isinstance(value, str):
                    revisions.add(value)
                elif name == 'down_revision' and isinstance(value, str):
                    down_revisions.add(value)
    heads = sorted(revisions - down_revisions)
    return heads[0] if len(heads) == 1 else None


def current_db_revision(env=None):
    """Revision recorded in the database, or None if unreachable/unmigrated."""
    env = os.environ if env is None else env
    url = env.get('DATABASE_URL')
    if not url:
        return None
    try:
        from sqlalchemy import create_engine
        engine = create_engine(url, connect_args={'connect_timeout': 2})
        with engine.connect() as connection:
            row = connection.execute(
                'SELECT version_num FROM alembic_version').fetchone()
        return row[0] if row else None
    except Exception:
        return None


def migrations_ready(env=None):
    """True only when the database is migrated to the head revision."""
    head = head_revision()
    return bool(head) and head == current_db_revision(env=env)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(role=None, env=None, compose_path=None):
    """Build the audit report shared by the CLI and the HTTP endpoint."""
    env = os.environ if env is None else env
    role = role or env.get('APP_ROLE') or 'cli'
    compose_path = compose_path if compose_path is not None else find_compose_file(env)
    compose_map = compose_env_map(compose_path, role)
    settings = _settings()

    keys = []
    resolved = {}
    for spec in CONFIG_SPEC:
        if spec.env is not None and spec.env in env:
            raw = env[spec.env]
            source = compose_map.get(spec.env, SOURCE_ENV)
        else:
            raw = spec.default
            source = SOURCE_DEFAULT
        if isinstance(raw, str) and '{' in raw:
            raw = raw.format(**resolved)
        value = _parse(spec.parser, raw)
        resolved[spec.name] = value

        settings_value = None
        status = 'ok'
        if spec.settings_attr is not None:
            settings_value = getattr(settings, spec.settings_attr, None)
            if settings_value != value:
                status = 'drift'

        keys.append({
            'name': spec.name,
            'value': value,
            'source': source,
            'overridden': source != SOURCE_DEFAULT,
            'settings_value': settings_value,
            'status': status,
        })

    head = head_revision()
    current = current_db_revision(env=env)
    drift = [key['name'] for key in keys if key['status'] == 'drift']
    return {
        'role': role,
        'alembic': {
            'head': head,
            'current': current,
            'ready': bool(head) and head == current,
        },
        'keys': keys,
        'drift': drift,
        'ok': not drift,
        'compose_file': str(compose_path) if compose_path else None,
        'notes': NOTES,
    }


def format_text(report):
    """Human-readable rendering; one line per key for side-by-side diffs."""
    alembic = report['alembic']
    lines = [
        'role: {}'.format(report['role']),
        'alembic_head: {}'.format(alembic['head'] or 'unknown'),
        'alembic_current: {}'.format(alembic['current'] or 'unavailable'),
        'migrations_ready: {}'.format('yes' if alembic['ready'] else 'no'),
    ]
    for key in report['keys']:
        flag = '' if key['status'] == 'ok' else '  [DRIFT settings={!r}]'.format(
            key['settings_value'])
        lines.append('{}={!r} [{}]{}'.format(
            key['name'], key['value'], key['source'], flag))
    if report['drift']:
        lines.append('DRIFT detected on: {}'.format(', '.join(report['drift'])))
    return '\n'.join(lines)
