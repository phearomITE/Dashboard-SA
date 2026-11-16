FROM node:22-alpine AS dashboard-build
WORKDIR /ui
RUN npm install -g pnpm@10.32.1
COPY dashboard/package.json dashboard/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY dashboard ./
RUN pnpm build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && apt-get clean \
    && find /var/lib/apt/lists -mindepth 1 -delete

RUN groupadd --system bot && useradd --system --gid bot --home-dir /app bot

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY templates ./templates
COPY --from=dashboard-build /ui/dist ./dashboard/dist

RUN chown -R bot:bot /app
USER bot

EXPOSE 8080

CMD ["python", "-m", "app.main"]
