#!/bin/bash

# Run alembic migrations exactly once per deployment (idempotent and
# guarded by a Postgres advisory lock, so repeated/retried starts heal).
# Only the worker migrates; the web service never does.
poetry run python -m src.migrate

if [ ${DEV:-False} = 'true' ]; then
  # Run celery in the background
  # NOTE: -B is for the heartbeat task scheduler
  poetry run celery -B \
    -A src.entry.celery worker \
    --loglevel=info \
    --pidfile=/var/run/celery/worker.pid
else
  # Run celery in the foreground
  poetry run celery -B \
    -A src.entry.celery worker \
    --loglevel=info
fi
