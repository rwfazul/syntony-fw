FROM python:3.12-slim

WORKDIR /app

COPY runtime/executor/requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir \
    --requirement /tmp/requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY runtime/ ./runtime/

RUN pip install --no-cache-dir \
    --no-deps .

COPY config/ ./config/
COPY specifications/ ./specifications/
COPY deployment/examples/local/ ./deployment/examples/local/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "runtime", "executor"]
