#!/bin/sh
# Start command for the free demo (render.demo.yaml): one container runs everything.
# Render keeps the quotes of an inline `sh -c "..."` command, so the steps live here instead.
set -e
# Fewer malloc arenas keep a threaded Python process from holding on to memory it freed.
export MALLOC_ARENA_MAX=2

flask --app wsgi db upgrade
# Bucket settings are best effort; a token limited to objects may not be allowed to change them.
flask --app wsgi storage bootstrap || echo "storage bootstrap failed; continuing"
if [ "${DEMO_RESET_ON_START:-false}" = "true" ]; then
  flask --app wsgi reset-demo --yes
else
  flask --app wsgi seed
fi

# Free plans have no background workers, so the worker runs beside the web app, with the periodic
# scheduler as a thread inside it: a third process would not fit in the plan's 512 MB.
python -m app.jobs.worker --with-periodic &

exec gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind "0.0.0.0:${PORT:-8000}"
