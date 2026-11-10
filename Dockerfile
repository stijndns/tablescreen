# The gateway (the player webapp's server) in a container; started with
# compose.yaml. Holds only the gateway and the bridge's wire format: never
# the app, and never the api/ folder (see .dockerignore).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TABLESCREEN_GATEWAY_DATA=/data \
    TABLESCREEN_GATEWAY_IN_CONTAINER=1

WORKDIR /app
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY bridge_protocol.py ./
COPY gateway/ gateway/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin gateway
USER gateway

EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8443), 2)"]
CMD ["python", "-m", "gateway"]
