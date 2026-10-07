"""Application entrypoint.

Imported by Flask / uWSGI / Celery to serve the app (the app object is
booted lazily via module ``__getattr__`` so importing this module is free
of side effects), and runnable as a CLI:

    python -m src.entry audit [--json]         # from backend/
    python -m backend.src.entry audit --json   # from the repo root

The ``audit`` subcommand only calls ``src.audit`` — the single
implementation of the configuration audit contract — and exits non-zero
when the two parse paths (this module's resolution vs. ``src.settings``)
have drifted apart.
"""

import argparse
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='entry')
    subparsers = parser.add_subparsers(dest='command')
    audit_parser = subparsers.add_parser(
        'audit', help='Print the configuration audit report')
    audit_parser.add_argument(
        '--json', action='store_true', help='Emit the report as JSON')
    args = parser.parse_args(argv)

    if args.command == 'audit':
        from src import audit
        report = audit.build_report(role=os.getenv('APP_ROLE', 'cli'))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(audit.format_text(report))
        return 0 if report['ok'] else 1

    parser.print_help()
    return 2


def __getattr__(name):
    # Boot the Flask/Celery app only when a serving runtime actually asks
    # for it (flask CLI, uwsgi --callable, celery -A src.entry.celery).
    if name in ('app', 'celery', 'flask_app'):
        from src.app import Application
        app = Application()
        globals()['app'] = app
        globals()['celery'] = app.celery
        globals()['flask_app'] = app.flask_app
        return globals()[name]
    raise AttributeError('module {!r} has no attribute {!r}'.format(
        __name__, name))


if __name__ == '__main__':
    sys.exit(main())
