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
# Grafana ships most of its data sources as separate plugins, each with its own Go binary and
# with that the known vulnerabilities of its Go version and modules. The stack only uses
# PostgreSQL, so the others go; the panels are part of Grafana itself and stay. Without the
# Prometheus plugin, the service that moves Prometheus data sources to the Azure and Amazon
# types logs an error on every start, so its feature toggle goes off; the grep fails the build
# if a newer image no longer has the section it goes in.
USER root
RUN find /usr/share/grafana/data/plugins-bundled -mindepth 1 -maxdepth 1 \
        ! -name grafana-postgresql-datasource -exec rm -rf {} + \
    && sed -i 's/^\[feature_toggles\]$/&\nprometheusTypeMigration = false/' /etc/grafana/grafana.ini \
    && grep -qx 'prometheusTypeMigration = false' /etc/grafana/grafana.ini
USER 472
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /etc/grafana/dashboards
COPY grafana/start.sh /usr/local/bin/lifedash-grafana-start
ENTRYPOINT ["/usr/local/bin/lifedash-grafana-start"]
