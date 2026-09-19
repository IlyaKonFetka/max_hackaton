# Этап 1: сборка мини-приложения (раздаётся бэкендом на /app/ — для проверки из compose без GitHub Pages)
FROM node:20-alpine AS miniapp
WORKDIR /src
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY miniapp/ ./
ENV VITE_BASE=/app/
RUN npm run build

# Этап 2: бэкенд + бот
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY rules/ rules/
COPY --from=miniapp /src/dist backend/static
ENV DATA_DIR=/data RULES_DIR=/srv/rules STATIC_DIR=/srv/backend/static PORT=8000
VOLUME ["/data"]
EXPOSE 8000
WORKDIR /srv/backend
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
