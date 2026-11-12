FROM python:3.11-slim

WORKDIR /opt/shawn-m1

# Persistent data dirs
RUN mkdir -p /opt/shawn-m1/logs /opt/shawn-m1/ledger /opt/shawn-m1/config

COPY server.py .
COPY config/ config/

# Ledger and logs are volumes — survive container restarts
VOLUME ["/opt/shawn-m1/ledger", "/opt/shawn-m1/logs"]

EXPOSE 8881

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8881/health')" || exit 1

CMD ["python3", "server.py"]
