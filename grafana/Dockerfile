# Grafana with the Life Dashboard data source and dashboards provisioned.
# Build from the repository root: docker build -f grafana/Dockerfile .
#
# The dashboards live under /etc/grafana, not /var/lib/grafana: that path is the
# grafana-data volume, which would keep the dashboards of the first version forever.
FROM grafana/grafana:13.2.4
LABEL org.opencontainers.image.title="Life Dashboard Grafana" \
      org.opencontainers.image.description="Grafana with the six Life Dashboard dashboards and the Postgres data source provisioned" \
      org.opencontainers.image.source="https://github.com/owen282000/life-dashboard-stack" \
      org.opencontainers.image.licenses="MIT"
ENV GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/dashboards/life-dashboard/overview.json \
    GF_PLUGINS_PREINSTALL_DISABLED=true
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /etc/grafana/dashboards
