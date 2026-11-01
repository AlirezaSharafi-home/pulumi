"""Builds the Grafana dashboard JSON for the Guestbook (pure Python, no Pulumi imports).

Metrics used (all scraped via the ServiceMonitor in __main__.py unless noted):
  apache_*          apache-exporter sidecar on the frontend pods
  redis_*           redis_exporter sidecar on redis-leader / redis-replica
  container_*       kubelet cAdvisor metrics (pod CPU / memory), no sidecar needed
"""

# The kube-prometheus-stack chart provisions its Prometheus datasource with uid "prometheus".
DS = {"type": "prometheus", "uid": "prometheus"}
NS = 'namespace="guestbook"'

UP_DOWN_MAPPING = [{
    "type": "value",
    "options": {
        "0": {"text": "DOWN", "color": "red", "index": 0},
        "1": {"text": "UP", "color": "green", "index": 1},
    },
}]


def _targets(exprs):
    return [
        {"refId": chr(65 + i), "datasource": DS, "expr": expr, "legendFormat": legend}
        for i, (expr, legend) in enumerate(exprs)
    ]


def _timeseries(pid, title, exprs, x, y, w=12, h=8, unit="short", description=""):
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"fillOpacity": 10}}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "targets": _targets(exprs),
    }


def _stat(pid, title, expr, x, unit="short", mappings=None, thresholds=None, description=""):
    steps = thresholds or [{"color": "green", "value": None}]
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": 6, "h": 4},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "mappings": mappings or [],
                "thresholds": {"mode": "absolute", "steps": steps},
            },
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "none"},
        "targets": _targets([(expr, "")]),
    }


def build_dashboard():
    panels = [
        # ---- Row 1: at-a-glance health ----
        _stat(1, "Scrape targets UP", f"sum(up{{{NS}}})", 0,
              thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 5}],
              description="Expected: 3 frontend + redis-leader + redis-replica = 5"),
        _stat(2, "Frontend requests / s", f"sum(rate(apache_accesses_total{{{NS}}}[1m]))", 6,
              unit="reqps"),
        _stat(3, "Frontend (Apache) up", f"min(apache_up{{{NS}}})", 12, mappings=UP_DOWN_MAPPING,
              thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}]),
        _stat(4, "Redis replica linked to leader", f'min(redis_master_link_up{{{NS}, job="redis-replica"}})', 18,
              mappings=UP_DOWN_MAPPING,
              thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
              description="DOWN means reads (served by the replica) will be empty or stale"),
        # ---- Row 2: frontend ----
        _timeseries(5, "Frontend request rate (per pod)",
                    [(f"sum by (pod) (rate(apache_accesses_total{{{NS}}}[1m]))", "{{pod}}")],
                    0, 4, unit="reqps",
                    description="Includes the exporter's own scrapes of /server-status"),
        _timeseries(6, "Apache workers (busy vs idle)",
                    [(f'sum(apache_workers{{{NS}, state="busy"}})', "busy"),
                     (f'sum(apache_workers{{{NS}, state="idle"}})', "idle")],
                    12, 4),
        # ---- Row 3: redis ----
        _timeseries(7, "Redis commands / s",
                    [(f"sum by (job) (rate(redis_commands_processed_total{{{NS}}}[1m]))", "{{job}}")],
                    0, 12, unit="ops"),
        _timeseries(8, "Redis connected clients",
                    [(f"sum by (job) (redis_connected_clients{{{NS}}})", "{{job}}")],
                    12, 12),
        # ---- Row 4: pod resource usage ----
        _timeseries(9, "Pod CPU usage (cores)",
                    [(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{NS}, container!=""}}[5m]))', "{{pod}}")],
                    0, 20, unit="none"),
        _timeseries(10, "Pod memory (working set)",
                    [(f'sum by (pod) (container_memory_working_set_bytes{{{NS}, container!=""}})', "{{pod}}")],
                    12, 20, unit="bytes"),
        # ---- Row 5: errors and redis memory ----
        _timeseries(11, "Redis rejected connections / s (errors)",
                    [(f"sum by (job) (rate(redis_rejected_connections_total{{{NS}}}[5m]))", "{{job}}")],
                    0, 28,
                    description="Connections refused because maxclients was hit; should stay at 0"),
        _timeseries(12, "Redis memory used",
                    [(f"sum by (job) (redis_memory_used_bytes{{{NS}}})", "{{job}}")],
                    12, 28, unit="bytes"),
    ]
    return {
        "uid": "guestbook",
        "title": "Guestbook",
        "tags": ["guestbook"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": panels,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_dashboard(), indent=2))
