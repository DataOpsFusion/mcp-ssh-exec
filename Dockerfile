FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOSTS_CONFIG=/app/hosts.yaml \
    PORT=8890 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv
COPY server.py /app/server.py
COPY ssh_helpers.py /app/ssh_helpers.py
COPY settings.py /app/settings.py
COPY hosts.yaml /app/hosts.yaml
COPY services /app/services

EXPOSE 8890

CMD ["python", "server.py"]
