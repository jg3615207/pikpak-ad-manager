FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if required by zhconv or sqlite
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set environment variables for the paths
ENV CONFIG_DIR=/config
ENV TARGET_DIR=/data
ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "main.py"]
