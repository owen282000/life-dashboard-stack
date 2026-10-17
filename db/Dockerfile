# Postgres with the guard that refuses to start while an old Postgres 16 volume still holds
# the data (see tools/pg-guard.sh). The upgrade script that moves that data over ships in
# the image too, for installs without a clone of the repository.
# Build from the repository root: docker build -f db/Dockerfile .
FROM postgres:18.6-alpine
LABEL org.opencontainers.image.title="Life Dashboard database" \
      org.opencontainers.image.description="Postgres for the Life Dashboard Stack, with a guard for data left in a Postgres 16 volume" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
# The official entrypoint drops from root to postgres with `gosu postgres ...`. gosu is built
# with an old Go and carries its known vulnerabilities; Alpine's su-exec does the same as a
# small C program, so the entrypoint calls su-exec instead and gosu goes. The grep fails the
# build if a newer base image calls gosu in a way this does not catch.
RUN apk add --no-cache su-exec \
    && sed -i 's/exec gosu postgres /exec su-exec postgres /' \
        /usr/local/bin/docker-entrypoint.sh /usr/local/bin/docker-ensure-initdb.sh \
    && rm /usr/local/bin/gosu \
    && ! grep -rl gosu /usr/local/bin
COPY --chmod=755 tools/pg-guard.sh /usr/local/bin/pg-guard.sh
COPY --chmod=755 tools/upgrade-postgres.sh /usr/local/share/life-dashboard/upgrade-postgres.sh
ENTRYPOINT ["pg-guard.sh"]
CMD ["postgres"]
