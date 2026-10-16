# Built from the repo root (see compose.yaml): needs both api/ and web/.

# Stage 1: build the SvelteKit app into static files.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the API, which also serves the built app.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_DIR=/srv/web

WORKDIR /srv

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY api/alembic.ini ./alembic.ini
COPY api/migrations ./migrations
COPY --from=web /web/build ./web

# Run as an unprivileged user, never root.
RUN useradd --system --uid 10001 --no-create-home app
# Garmin tokens (the garmin service mounts a named volume here). Created in the image so a new volume
# starts owned by the app user with mode 0700: nobody else in the container can read the tokens.
RUN install -d -o 10001 -g 10001 -m 0700 /garmin
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
