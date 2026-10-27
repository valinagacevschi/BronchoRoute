FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LUNGNAV_HOST=0.0.0.0 \
    LUNGNAV_PORT=8765 \
    LUNGNAV_DATA_ROOT=/data \
    LUNGNAV_CACHE_DIR=/cache \
    LUNGNAV_VIEWER_ONLY=1 \
    LUNGNAV_CASES=local-001 \
    LUNGNAV_ALLOWED_HOSTS=lung-nav.appcenter.ro,127.0.0.1,localhost

WORKDIR /app

RUN useradd --system --uid 10001 --user-group --create-home lungnav \
    && mkdir -p /cache /tmp \
    && chown -R 10001:10001 /cache /tmp

COPY deploy/requirements-server.txt /app/deploy/requirements-server.txt
RUN pip install --no-cache-dir -r /app/deploy/requirements-server.txt

COPY app /app/app
COPY prototype /app/prototype
COPY pipeline/atomic_json.py \
     pipeline/import_dicom.py \
     pipeline/make_prototype_data.py \
     pipeline/paths.py \
     pipeline/repo_root.py \
     pipeline/review_signoff.py \
     pipeline/route_frame.py \
     pipeline/run_totalsegmentator.py \
     pipeline/snomed.py \
     pipeline/app_server.py \
     /app/pipeline/

ENV PYTHONPATH=/app:/app/pipeline

USER 10001:10001
EXPOSE 8765

CMD ["python", "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8765"]
