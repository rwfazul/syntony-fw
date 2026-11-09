FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY runtime/ ./runtime/

RUN pip install --no-cache-dir .

COPY config/ ./config/
COPY specifications/ ./specifications/
COPY deployment/examples/local/ ./deployment/examples/local/
COPY deployment/examples/monitors/ ./deployment/examples/monitors/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "runtime", "validate"]
