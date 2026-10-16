# Postgres with the guard that refuses to start while an old Postgres 16 volume still holds
# the data (see tools/pg-guard.sh). The upgrade script that moves that data over ships in
# the image too, for installs without a clone of the repository.
# Build from the repository root: docker build -f db/Dockerfile .
FROM postgres:18.6-alpine
LABEL org.opencontainers.image.title="Life Dashboard database" \
      org.opencontainers.image.description="Postgres for the Life Dashboard Stack, with a guard for data left in a Postgres 16 volume" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
COPY --chmod=755 tools/pg-guard.sh /usr/local/bin/pg-guard.sh
COPY --chmod=755 tools/upgrade-postgres.sh /usr/local/share/life-dashboard/upgrade-postgres.sh
ENTRYPOINT ["pg-guard.sh"]
CMD ["postgres"]
