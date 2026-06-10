FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" && pip install --no-cache-dir uvicorn

# Copy source
COPY src/ src/
COPY tests/ tests/
COPY README.md .

EXPOSE 8000

CMD ["uvicorn", "cr_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
