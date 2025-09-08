# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy metadata + code BEFORE install
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY rules /app/rules

# Install deps (editable mode reads pyproject + src/)
RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
