FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    CAMPUSFIX_DB=/data/campusfix.db \
    UPLOAD_FOLDER=/data/uploads

WORKDIR /app

RUN groupadd --system --gid 10001 campusfix \
    && useradd --system --uid 10001 --gid campusfix --home-dir /app campusfix

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=campusfix:campusfix app.py config.py database.py gunicorn.conf.py ./
COPY --chown=campusfix:campusfix templates ./templates
COPY --chown=campusfix:campusfix static ./static
COPY --chown=campusfix:campusfix tests ./tests
RUN mkdir -p /data /app/instance && chown -R campusfix:campusfix /data /app

USER 10001:10001
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:5000/health', timeout=3)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]