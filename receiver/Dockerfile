# The receiver, plus the sample data script for the demo profile.
# Build from the repository root: docker build -f receiver/Dockerfile .
FROM python:3.14.8-slim
LABEL org.opencontainers.image.title="Life Dashboard receiver" \
      org.opencontainers.image.description="Takes signed payloads from the Life Dashboard Companion apps and stores them in Postgres" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY receiver/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receiver/receiver.py receiver/pairing.py receiver/api.py receiver/queries.py receiver/roles.py receiver/views.sql ./
COPY receiver/migrations ./migrations
# The demo profile runs: python tools/sample_data.py
COPY tools/sample_data.py ./tools/
# It writes no files, so it runs as nobody, by number, so Kubernetes can tell it's not root.
USER 65534:65534
EXPOSE 8080
CMD ["python", "receiver.py"]
