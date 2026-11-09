#!/bin/sh
# Start command for the free demo (render.demo.yaml): one container runs everything.
# Render keeps the quotes of an inline `sh -c "..."` command, so the steps live here instead.
set -e

flask --app wsgi db upgrade
flask --app wsgi storage bootstrap
flask --app wsgi seed

# Free plans have no background workers, so the scheduler and the worker run beside the web app.
python -m app.jobs.scheduler &
python -m app.jobs.worker &

exec gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind "0.0.0.0:${PORT:-8000}"
