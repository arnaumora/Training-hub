# Built from the repo root (see compose.yaml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/collector ./collector

# Run as an unprivileged user, never root. Same uid as the API so file ownership is uniform.
RUN useradd --system --uid 10001 --no-create-home app
USER app

CMD ["python", "-m", "collector"]
