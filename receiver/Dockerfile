# The receiver, plus the sample data script for the demo profile.
# Build from the repository root: docker build -f receiver/Dockerfile .
# Alpine, not Debian slim: slim carries util-linux, ncurses, perl and systemd libraries with
# known vulnerabilities that have no fix, and Python here needs none of them. Alpine has tzdata
# for zoneinfo, and sh but no bash.
FROM python:3.14.8-alpine
LABEL org.opencontainers.image.title="Life Dashboard receiver" \
      org.opencontainers.image.description="Takes signed payloads from the Life Dashboard Companion apps and stores them in Postgres" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY receiver/requirements.txt .
# apk upgrade brings Alpine's fixes that came out after the Python image was built.
# Wheels only: a package without a musl wheel fails the build instead of needing a compiler.
# Then pip goes: nothing in the container runs it, and the packages it vendors, such as urllib3,
# bring known vulnerabilities of their own.
RUN apk upgrade --no-cache \
    && pip install --no-cache-dir --only-binary=:all: --root-user-action=ignore -r requirements.txt \
    && pip uninstall --yes --root-user-action=ignore pip
COPY receiver/receiver.py receiver/pairing.py receiver/api.py receiver/queries.py receiver/roles.py receiver/doctor.py receiver/views.sql ./
COPY receiver/migrations ./migrations
# The demo profile runs: python tools/sample_data.py
COPY tools/sample_data.py ./tools/
# It writes no files, so it runs as nobody, by number, so Kubernetes can tell it's not root.
USER 65534:65534
EXPOSE 8080
CMD ["python", "receiver.py"]
