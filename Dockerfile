# 1) фронт → 2) колёса Python → 3) slim-рантайм без node, компиляторов и pytest

FROM node:20.18.1-alpine3.20 AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12.8-slim-bookworm AS wheels
WORKDIR /src
COPY requirements.txt .
# тестовые зависимости (pytest*) в рантайм не идут
RUN grep -viE '^(pytest)' requirements.txt > requirements.runtime.txt \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.runtime.txt

FROM python:3.12.8-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_DIR=/srv/web/dist
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels \
    && useradd --system --uid 10001 --no-create-home app
WORKDIR /srv
COPY app/ ./app/
COPY db/ ./db/
COPY data/ ./data/
# сценарии и независимые проверки: нужны рантайму для GET /api/metrics (трек A)
COPY eval/ ./eval/
COPY --from=web /src/web/dist ./web/dist
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)"
# один воркер: блокировка сессии в процессе (см. app/main.py)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8080} --workers 1 --proxy-headers"]

# тесты: отдельный образ с pytest, в рантайм не попадает
FROM runtime AS test
USER root
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY tests/ ./tests/
USER 10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]

# образ по умолчанию
FROM runtime
