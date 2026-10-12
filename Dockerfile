FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG APP_BASE_PATH=/weibo/
RUN APP_BASE_PATH=${APP_BASE_PATH} npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_BASE_PATH=/weibo
WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ /app/server/
COPY weibospider/research*.py weibospider/research_queries.json /app/weibospider/
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --uid 10001 --create-home app
USER app
EXPOSE 8877
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8877/'+os.environ.get('APP_BASE_PATH','').strip('/')+'/api/health',timeout=5)"
# Default container runs the platform only. Collection and DB initialization
# require explicit command overrides (see compose.yaml profiles).
CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8877", "--workers", "1"]
