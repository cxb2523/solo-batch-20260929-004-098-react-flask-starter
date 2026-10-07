"""Run alembic migrations once, from the worker only.

Invoked by ``filesystem/entrypoints/worker.sh`` before Celery starts.  A
Postgres advisory lock serialises concurrent/repeated worker starts, and
``flask db upgrade`` itself is idempotent, so first boot, duplicate boots
and mid-way retries all converge to the head revision (self-healing).
The web service never runs this — it serves 503 until the database is
ready (see ``src.routes.counter``).
"""

from flask_migrate import upgrade

from src.app import Application

# Arbitrary but stable advisory-lock key for "run alembic migrations".
LOCK_KEY = 727272


def main():
    app = Application()
    with app.flask_app.app_context():
        with app.db.engine.connect() as connection:
            connection.execute('SELECT pg_advisory_lock({})'.format(LOCK_KEY))
            try:
                upgrade()
            finally:
                connection.execute('SELECT pg_advisory_unlock({})'.format(LOCK_KEY))


if __name__ == '__main__':
    main()
