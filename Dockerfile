FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

# uv — drop-in faster pip with a real resolver
RUN pip install --no-cache-dir uv

# Copy project and install in a single pass
COPY pyproject.toml .
COPY src/ src/
RUN uv pip install --system --no-cache -e .

# Initialize data directories and mempalace config
RUN mkdir -p /data/mempalace /data/yeti/images /root/.mempalace \
    && echo '{"palace_path": "/data/mempalace", "collection_name": "mempalace_drawers"}' > /root/.mempalace/config.json

EXPOSE 8000

# Default: run the API server
CMD ["uvicorn", "yeti.app:app", "--host", "0.0.0.0", "--port", "8000"]
