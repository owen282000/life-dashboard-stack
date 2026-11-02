# The read-only MCP server, with the receiver's query module.
# Build from the repository root: docker build -f mcp/Dockerfile .
# The MCP Registry lists it under the name in io.modelcontextprotocol.server.name, which must
# equal "name" in server.json.
# Alpine, not Debian slim: slim carries util-linux, ncurses, perl and systemd libraries with
# known vulnerabilities that have no fix, and Python here needs none of them. Alpine has tzdata
# for zoneinfo, and sh but no bash.
FROM python:3.15.0-alpine
LABEL org.opencontainers.image.title="Life Dashboard MCP server" \
      org.opencontainers.image.description="Read-only MCP server that lets a model of your choice read the Life Dashboard views" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.owen282000/life-dashboard"
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY mcp/requirements.txt .
# apk upgrade brings Alpine's fixes that came out after the Python image was built.
# Wheels only: a package without a musl wheel fails the build instead of needing a compiler.
# Then pip goes: nothing in the container runs it, and the packages it vendors, such as urllib3,
# bring known vulnerabilities of their own.
RUN apk upgrade --no-cache \
    && pip install --no-cache-dir --only-binary=:all: --root-user-action=ignore -r requirements.txt \
    && pip uninstall --yes --root-user-action=ignore pip
COPY receiver/queries.py mcp/server.py ./
# nobody, by number, so Kubernetes can tell it's not root.
USER 65534:65534
EXPOSE 8090
CMD ["python", "server.py"]
