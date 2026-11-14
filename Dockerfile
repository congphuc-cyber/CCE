FROM python:3.12-slim

# Thiết lập biến môi trường Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001 \
    DJANGO_SETTINGS_MODULE=site1.settings

WORKDIR /app

# Cài đặt công cụ hệ thống tối thiểu
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Cài đặt thư viện Python
COPY requirements/ /app/requirements/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements/base.txt

# Sao chép mã nguồn dự án
COPY site1/ /app/site1/
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
RUN chmod +x /app/scripts/entrypoint.sh

# Tạo thư mục dữ liệu và static
RUN mkdir -p /app/site1/media /app/site1/staticfiles

EXPOSE 8001

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
