"""Step 2: Prometheus + Grafana (kube-prometheus-stack Helm chart), in their own namespace."""

import pulumi
import json

from pulumi_kubernetes.core.v1 import ConfigMap, Namespace, Service
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs

config = pulumi.Config()
# Own namespace (not the common "monitoring") so it can't collide with an existing stack.
monitoring_ns_name = config.get("monitoringNamespace") or "guestbook-monitoring"
# Pinned for reproducibility. Check for newer: helm search repo prometheus-community/kube-prometheus-stack
chart_version = config.get("kubePrometheusStackVersion") or "91.9.0"
# Set to true on clusters that ALREADY run a Prometheus Operator (CRDs are cluster-wide, and two
# operators would fight over the same objects). We then deploy only our own Prometheus + Grafana
# and let the existing operator reconcile them.
reuse_existing_operator = config.get_bool("reuseExistingOperator") or False
# How Grafana is exposed: LoadBalancer | NodePort | ClusterIP
grafana_service_type = config.get("grafanaServiceType") or "LoadBalancer"
grafana_admin_user = "admin"
# Chart default is "prom-operator". Override with: pulumi config set --secret grafanaAdminPassword <pw>
grafana_admin_password = config.get_secret("grafanaAdminPassword") or "prom-operator"

monitoring_ns = Namespace("monitoring-ns", metadata={"name": monitoring_ns_name})

# kube-prometheus-stack bundles: Prometheus Operator (+ CRDs such as ServiceMonitor), Prometheus,
# Grafana (with Prometheus pre-wired as a datasource), node-exporter and kube-state-metrics.
values = {
    # Unique names so nothing collides with other releases, incl. cluster-scoped objects.
    # Services: gb-monitoring-prometheus (9090) and gb-grafana (80)
    "fullnameOverride": "gb-monitoring",
    "grafana": {
        "fullnameOverride": "gb-grafana",
        "service": {"type": grafana_service_type},
        "adminUser": grafana_admin_user,
        "adminPassword": grafana_admin_password,
        # Recent Grafana versions try to auto-update their bundled plugins at startup. On the
        # chart's read-only container filesystem that fails ("unlinkat ...: read-only file system")
        # and leaves the Prometheus datasource plugin unregistered ("Could not find plugin
        # definition for data source"). Disable the background plugin installer/updater.
        "env": {
            "GF_PLUGINS_PREINSTALL_AUTO_UPDATE": "false",
            "GF_PLUGINS_PREINSTALL_DISABLED": "true",
        },
    },
    "prometheus": {
        "prometheusSpec": {
            # Discover ServiceMonitors/PodMonitors in ANY namespace (e.g. guestbook),
            # not only those labelled with this Helm release.
            "serviceMonitorSelectorNilUsesHelmValues": False,
            "podMonitorSelectorNilUsesHelmValues": False,
            "retention": "7d",
        },
    },
    # Not needed for this exercise; keeps the footprint small.
    "alertmanager": {"enabled": False},
    # Control-plane components are usually not scrapeable on self-managed clusters
    # and would show up as permanently "down" targets.
    "kubeEtcd": {"enabled": False},
    "kubeControllerManager": {"enabled": False},
    "kubeScheduler": {"enabled": False},
    "kubeProxy": {"enabled": False},
}

if reuse_existing_operator:
    values["crds"] = {"enabled": False}                # CRDs already installed cluster-wide
    values["prometheusOperator"] = {"enabled": False}  # existing operator reconciles our Prometheus
    values["kubeStateMetrics"] = {"enabled": False}    # already running elsewhere
    values["nodeExporter"] = {"enabled": False}        # avoids host port 9100 conflict

kube_prometheus_stack = Release(
    "gb-monitoring",
    ReleaseArgs(
        name="gb-monitoring",
        chart="kube-prometheus-stack",
        version=chart_version,
        namespace=monitoring_ns.metadata["name"],
        repository_opts=RepositoryOptsArgs(
            repo="https://prometheus-community.github.io/helm-charts",
        ),
        timeout=600,  # seconds; the first install pulls several images
        values=values,
    ),
)

# Stretch goal: Guestbook dashboard, provisioned from code. Grafana's dashboard sidecar watches for
# ConfigMaps labelled grafana_dashboard=1 and loads them automatically.
import guestbook_dashboard  # noqa: E402

guestbook_dashboard_cm = ConfigMap(
    "guestbook-dashboard",
    metadata={
        "name": "guestbook-dashboard",
        "namespace": monitoring_ns.metadata["name"],
        "labels": {"grafana_dashboard": "1"},
    },
    data={"guestbook-dashboard.json": json.dumps(guestbook_dashboard.build_dashboard())},
    opts=pulumi.ResourceOptions(depends_on=[kube_prometheus_stack]),
)

# Read back the Grafana Service (after the release is installed) to learn its address.
grafana_svc = Service.get(
    "gb-grafana-svc",
    pulumi.Output.concat(monitoring_ns.metadata["name"], "/gb-grafana"),
    opts=pulumi.ResourceOptions(depends_on=[kube_prometheus_stack]),
)


def _grafana_url(args):
    spec, status = args
    if grafana_service_type == "LoadBalancer":
        ingress = ((status or {}).get("load_balancer") or {}).get("ingress") or []
        if ingress:
            host = ingress[0].get("ip") or ingress[0].get("hostname")
            return f"http://{host}"
        return "pending: no LoadBalancer address assigned yet (run `pulumi refresh`)"
    if grafana_service_type == "NodePort":
        return f"http://<any-node-ip>:{spec['ports'][0].get('node_port')}"
    return "ClusterIP only: kubectl -n guestbook-monitoring port-forward svc/gb-grafana 3000:80"


pulumi.export("monitoring_namespace", monitoring_ns.metadata["name"])
pulumi.export(
    "grafana_url",
    pulumi.Output.all(grafana_svc.spec, grafana_svc.status).apply(_grafana_url),
)
pulumi.export("grafana_admin_user", grafana_admin_user)
pulumi.export("grafana_admin_password", grafana_admin_password)
