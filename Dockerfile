FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY src/ src/

# Install the package in editable mode for development
RUN pip install --no-cache-dir -e .

# Initialize mempalace data directory and config
RUN mkdir -p /data/mempalace /root/.mempalace \
    && echo '{"palace_path": "/data/mempalace", "collection_name": "mempalace_drawers"}' > /root/.mempalace/config.json

EXPOSE 8000

# Default: run the API server
CMD ["uvicorn", "yeti.app:app", "--host", "0.0.0.0", "--port", "8000"]
