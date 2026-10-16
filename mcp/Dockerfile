# The read-only MCP server, with the receiver's query module.
# Build from the repository root: docker build -f mcp/Dockerfile .
FROM python:3.14.8-slim
LABEL org.opencontainers.image.title="Life Dashboard MCP server" \
      org.opencontainers.image.description="Read-only MCP server that lets a model of your choice read the Life Dashboard views" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receiver/queries.py mcp/server.py ./
# nobody, by number, so Kubernetes can tell it's not root.
USER 65534:65534
EXPOSE 8090
CMD ["python", "server.py"]
