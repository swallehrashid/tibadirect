# syntax=docker/dockerfile:1
# One image runs the web service, the worker and the scheduler; only the command differs.

# Stage 1: the web app. Until Phase 4 builds the real one, this carries a placeholder page.
# Phase 4 replaces this stage with a node:20-alpine build that leaves its result in /dist.
FROM scratch AS frontend
COPY backend/app/static/placeholder/ /dist/

# Stage 2: the API, with the system tools the bill checks need.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 tiba

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY data/lexicons/ data/lexicons/
COPY data/synthetic/ data/synthetic/
COPY --from=frontend /dist/ frontend/dist/

# The appeal checker's trained model is not kept in git; training it takes seconds and needs no database.
RUN cd backend && SYNTHETIC_DATA_DIR=/app/data/synthetic LEXICON_DIR=/app/data/lexicons \
    flask --app wsgi ai train-appeals

USER tiba
WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000
# Migrate, then serve. Render sets PORT; the worker overrides this command.
CMD ["sh", "-c", "flask --app wsgi db upgrade && exec gunicorn wsgi:app --workers 2 --threads 4 --bind 0.0.0.0:${PORT}"]
