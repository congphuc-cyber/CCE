
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=site1.settings.prod

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies for production
COPY requirements/ /app/requirements/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements/prod.txt

# Project source code
COPY site1/ /app/site1/
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh

RUN chmod +x /app/scripts/entrypoint.sh && \
    mkdir -p /app/site1/media /app/site1/staticfiles

WORKDIR /app/site1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
