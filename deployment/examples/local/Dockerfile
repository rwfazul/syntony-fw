FROM python:3.12-slim

WORKDIR /app

COPY deployment/examples/local/ ./deployment/examples/local/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "deployment/examples/local/resilience_system.py", "supervisor"]
