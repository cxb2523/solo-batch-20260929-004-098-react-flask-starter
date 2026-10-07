import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src import audit

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

ENV_VARS = [
    'DEV', 'REDIS_HOST', 'AES_SECRET_KEY', 'DATABASE_URL',
    'FLASK_DEBUG', 'LOCALSTACK_HOST', 'APP_ROLE', 'AUDIT_COMPOSE_FILE',
]


@pytest.fixture()
def clean_env(monkeypatch):
    """Neutralise the process env and re-evaluate Settings against it."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    import src.settings
    importlib.reload(src.settings)
    yield


def keys_by_name(report):
    return {key['name']: key for key in report['keys']}


def test_default_layer(clean_env):
    report = audit.build_report(role='cli')
    keys = keys_by_name(report)
    assert keys['REDIS_HOST']['value'] == 'redis'
    assert keys['REDIS_HOST']['source'] == audit.SOURCE_DEFAULT
    assert keys['REDIS_HOST']['overridden'] is False
    assert keys['AES_SECRET_KEY']['value'] == 'fake-aes-key'
    assert keys['DEV']['value'] is False
    assert keys['SQLALCHEMY_DATABASE_URI']['value'] is None
    assert report['ok'] is True
    assert report['drift'] == []


def test_environment_variable_layer(clean_env, monkeypatch):
    # REDIS_HOST is not injected by compose, so a plain env var wins
    # attribution as "environment variable" and derived keys follow.
    monkeypatch.setenv('REDIS_HOST', 'myredis')
    import src.settings
    importlib.reload(src.settings)
    report = audit.build_report(role='cli')
    keys = keys_by_name(report)
    assert keys['REDIS_HOST']['source'] == audit.SOURCE_ENV
    assert keys['REDIS_HOST']['value'] == 'myredis'
    assert keys['REDIS_HOST']['overridden'] is True
    assert keys['REDIS_URL']['value'] == 'redis://myredis:6379'
    assert keys['CELERY_BROKER_URL']['value'] == 'redis://myredis:6379'
    assert report['ok'] is True


def test_compose_layers(clean_env, monkeypatch):
    # DATABASE_URL lives in compose `environment:`, FLASK_DEBUG in
    # backend/backend.env (`env_file:`) — attribution must reflect that.
    monkeypatch.setenv('DATABASE_URL', 'postgres://u:p@postgres/app')
    monkeypatch.setenv('FLASK_DEBUG', '1')
    import src.settings
    importlib.reload(src.settings)
    report = audit.build_report(role='web')
    keys = keys_by_name(report)
    assert keys['SQLALCHEMY_DATABASE_URI']['source'] == audit.SOURCE_COMPOSE_ENV
    assert keys['SQLALCHEMY_DATABASE_URI']['value'] == 'postgres://u:p@postgres/app'
    assert keys['FLASK_DEBUG']['source'] == audit.SOURCE_COMPOSE_ENV_FILE
    assert report['ok'] is True


def test_compose_parser_covers_app_services():
    services = audit.parse_compose_services(REPO_ROOT / 'docker-compose.yml')
    for name in ('backend', 'worker'):
        assert 'DATABASE_URL' in services[name]['environment']
        assert 'APP_ROLE' in services[name]['environment']
        assert 'FLASK_DEBUG' in services[name]['env_file']
        assert 'LOCALSTACK_HOST' in services[name]['env_file']


def test_drift_is_detected(clean_env, monkeypatch):
    from src.settings import Settings
    monkeypatch.setattr(Settings, 'REDIS_HOST', 'tampered')
    report = audit.build_report(role='cli')
    keys = keys_by_name(report)
    assert keys['REDIS_HOST']['status'] == 'drift'
    assert keys['REDIS_HOST']['settings_value'] == 'tampered'
    assert 'REDIS_HOST' in report['drift']
    assert report['ok'] is False


def test_alembic_head_revision():
    assert audit.head_revision() == 'fb060a24643d'


def test_migrations_readiness(clean_env, monkeypatch):
    monkeypatch.setattr(audit, 'current_db_revision', lambda env=None: None)
    assert audit.migrations_ready() is False
    monkeypatch.setattr(
        audit, 'current_db_revision', lambda env=None: audit.head_revision())
    assert audit.migrations_ready() is True


def test_report_shape(clean_env):
    report = audit.build_report(role='worker')
    assert report['role'] == 'worker'
    assert set(report['alembic']) == {'head', 'current', 'ready'}
    assert report['alembic']['ready'] is False  # no database in tests
    assert isinstance(report['notes'], list) and report['notes']
    names = [key['name'] for key in report['keys']]
    assert names == [spec.name for spec in audit.CONFIG_SPEC]


def run_cli(args, cwd, env=None):
    return subprocess.run(
        [sys.executable, '-m'] + args,
        cwd=str(cwd), capture_output=True, text=True, env=env)


def test_cli_audit_json_from_backend_dir(clean_env):
    result = run_cli(['src.entry', 'audit', '--json'], cwd=BACKEND_DIR)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['role'] == 'cli'
    assert report['ok'] is True
    assert {k['name'] for k in report['keys']} == {
        spec.name for spec in audit.CONFIG_SPEC}


def test_cli_audit_json_from_repo_root(clean_env):
    result = run_cli(['backend.src.entry', 'audit', '--json'], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['ok'] is True


def test_cli_audit_text_output(clean_env):
    result = run_cli(['src.entry', 'audit'], cwd=BACKEND_DIR)
    assert result.returncode == 0, result.stderr
    for spec in audit.CONFIG_SPEC:
        assert '{}='.format(spec.name) in result.stdout
    assert 'role: cli' in result.stdout


def test_cli_exits_nonzero_on_drift(clean_env, monkeypatch):
    from src import entry
    from src.settings import Settings
    monkeypatch.setattr(Settings, 'AES_SECRET_KEY', 'tampered')
    assert entry.main(['audit']) == 1
    assert entry.main(['audit', '--json']) == 1
