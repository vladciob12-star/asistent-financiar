FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/var/data/finante.sqlite3 \
    APP_HOST=0.0.0.0 \
    PORT=10000

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home finance \
    && mkdir -p /var/data \
    && chown finance:finance /var/data

COPY --chown=finance:finance . .
RUN chmod 0755 /app/scripts/container_entrypoint.sh

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','10000')+'/healthz',timeout=3)"

ENTRYPOINT ["/app/scripts/container_entrypoint.sh"]
CMD ["sh", "-c", "exec python /app/web_app.py --host \"${APP_HOST}\" --port \"${PORT}\" --database \"${DATABASE_PATH}\""]
