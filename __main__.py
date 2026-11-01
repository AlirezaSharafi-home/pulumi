import pulumi
from pulumi_kubernetes.apps.v1 import Deployment
from pulumi_kubernetes.core.v1 import Service, Namespace

# Create only services of type `ClusterIP`
# for clusters that don't support `LoadBalancer` services
config = pulumi.Config()
useLoadBalancer = config.get_bool("useLoadBalancer")

# ADDITION vs. the tutorial: run everything in its own namespace
# so it can't conflict with anything else on the cluster.
ns_name = config.get("namespace") or "guestbook"
ns = Namespace("guestbook-ns", metadata={"name": ns_name})
namespace = ns.metadata["name"]

# Images (override with `pulumi config set <key> <value>`).
# The unpinned `redis` image is too new for the old guestbook replica image to replicate from
# (replica fails with "Can't handle RDB format version"). Set redisImage to the tag that works for you.
redis_image = config.get("redisImage") or "redis:6.2"
redis_exporter_image = config.get("redisExporterImage") or "oliver006/redis_exporter:v1.62.0"
apache_exporter_image = config.get("apacheExporterImage") or "lusotycoon/apache-exporter:v1.0.10"

# ADDITION (step 3): metrics sidecars. Containers in a pod share localhost, so each exporter
# talks to the app over 127.0.0.1 and serves Prometheus metrics on its own port.
redis_exporter = {
    "name": "redis-exporter",
    "image": redis_exporter_image,  # scrapes redis at localhost:6379 by default
    "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}},
    "ports": [{"name": "metrics", "container_port": 9121}],
}
apache_exporter = {
    "name": "apache-exporter",
    "image": apache_exporter_image,
    "args": ["--scrape_uri=http://localhost:80/server-status?auto"],  # mod_status, allowed from localhost
    "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}},
    "ports": [{"name": "metrics", "container_port": 9117}],
}

#
# REDIS LEADER
#
redis_leader_labels = {
    "app": "redis-leader",
}

redis_leader_deployment = Deployment(
    "redis-leader",
    metadata={"namespace": namespace},
    spec={
        "selector": {
            "match_labels": redis_leader_labels,
        },
        "replicas": 1,
        "template": {
            "metadata": {
                "labels": redis_leader_labels,
            },
            "spec": {
                "containers": [{
                    "name": "redis-leader",
                    "image": redis_image,
                    "resources": {
                        "requests": {
                            "cpu": "100m",
                            "memory": "100Mi",
                        },
                    },
                    "ports": [{
                        "container_port": 6379,
                    }],
                }, redis_exporter],
            },
        },
    })

redis_leader_service = Service(
    "redis-leader",
    metadata={
        "name": "redis-leader",
        "namespace": namespace,
        "labels": redis_leader_labels,
    },
    spec={
        "ports": [{
            "port": 6379,
            "target_port": 6379,
        }],
        "selector": redis_leader_labels,
    })

#
# REDIS REPLICA
#
redis_replica_labels = {
    "app": "redis-replica",
}

redis_replica_deployment = Deployment(
    "redis-replica",
    metadata={"namespace": namespace},
    spec={
        "selector": {
            "match_labels": redis_replica_labels,
        },
        "replicas": 1,
        "template": {
            "metadata": {
                "labels": redis_replica_labels,
            },
            "spec": {
                "containers": [{
                    "name": "redis-replica",
                    "image": "pulumi/guestbook-redis-replica",
                    "resources": {
                        "requests": {
                            "cpu": "100m",
                            "memory": "100Mi",
                        },
                    },
                    "env": [{
                        "name": "GET_HOSTS_FROM",
                        "value": "dns",
                        # If your cluster config does not include a dns service, then to instead access an environment
                        # variable to find the leader's host, comment out the 'value: dns' line above, and
                        # uncomment the line below:
                        # "value": "env"
                    }],
                    "ports": [{
                        "container_port": 6379,
                    }],
                }, redis_exporter],
            },
        },
    })

redis_replica_service = Service(
    "redis-replica",
    metadata={
        "name": "redis-replica",
        "namespace": namespace,
        "labels": redis_replica_labels,
    },
    spec={
        "ports": [{
            "port": 6379,
            "target_port": 6379,
        }],
        "selector": redis_replica_labels,
    })

#
# FRONTEND
#
frontend_labels = {
    "app": "frontend",
}

frontend_deployment = Deployment(
    "frontend",
    metadata={"namespace": namespace},
    spec={
        "selector": {
            "match_labels": frontend_labels,
        },
        "replicas": 5,
        "template": {
            "metadata": {
                "labels": frontend_labels,
            },
            "spec": {
                "containers": [{
                    "name": "php-redis",
                    "image": "pulumi/guestbook-php-redis",
                    "resources": {
                        "requests": {
                            "cpu": "100m",
                            "memory": "100Mi",
                        },
                    },
                    "env": [{
                        "name": "GET_HOSTS_FROM",
                        "value": "dns",
                        # If your cluster config does not include a dns service, then to instead access an environment
                        # variable to find the leader's host, comment out the 'value: dns' line above, and
                        # uncomment the line below:
                        # "value": "env"
                    }],
                    "ports": [{
                        "container_port": 80,
                    }],
                }, apache_exporter],
            },
        },
    })

frontend_service = Service(
    "frontend",
    metadata={
        "name": "frontend",
        "namespace": namespace,
        "labels": frontend_labels,
    },
    spec={
        "type": "LoadBalancer" if useLoadBalancer else "ClusterIP",
        "ports": [{
            "port": 80,
        }],
        "selector": frontend_labels,
    })

frontend_ip = ""
if useLoadBalancer:
    ingress = frontend_service.status.apply(lambda status: status["load_balancer"]["ingress"][0])
    frontend_ip = ingress.apply(lambda ingress: ingress.get("ip", ingress.get("hostname", "")))
else:
    frontend_ip = frontend_service.spec.apply(lambda spec: spec.get("cluster_ip", ""))

pulumi.export("frontend_ip", frontend_ip)
pulumi.export("namespace", namespace)

#
# STEP 3: METRICS SERVICES (internal only)
#
# Separate ClusterIP Services expose just the exporter ports, so the metrics are never published
# on the frontend's public LoadBalancer address.
def metrics_service(app, selector_labels, port):
    return Service(
        f"{app}-metrics",
        metadata={
            "name": f"{app}-metrics",
            "namespace": namespace,
            "labels": {"app": app, "metrics": "true"},
        },
        spec={
            "type": "ClusterIP",
            "ports": [{"name": "metrics", "port": port, "target_port": "metrics"}],
            "selector": selector_labels,
        })


frontend_metrics_service = metrics_service("frontend", frontend_labels, 9117)
redis_leader_metrics_service = metrics_service("redis-leader", redis_leader_labels, 9121)
redis_replica_metrics_service = metrics_service("redis-replica", redis_replica_labels, 9121)

# Step 2: monitoring stack (Prometheus + Grafana), see monitoring.py
import monitoring  # noqa: E402

#
# STEP 3: SERVICEMONITOR
#
# One ServiceMonitor picks up every Service in the guestbook namespace labelled metrics=true
# (frontend, redis-leader, redis-replica) and scrapes its "metrics" port every 15s.
from pulumi_kubernetes.apiextensions import CustomResource  # noqa: E402

guestbook_service_monitor = CustomResource(
    "guestbook-servicemonitor",
    api_version="monitoring.coreos.com/v1",
    kind="ServiceMonitor",
    metadata={"name": "guestbook", "namespace": namespace},
    spec={
        "jobLabel": "app",  # job="frontend" / "redis-leader" / "redis-replica"
        "selector": {"matchLabels": {"metrics": "true"}},
        "namespaceSelector": {"matchNames": [ns_name]},
        "endpoints": [{"port": "metrics", "interval": "15s"}],
    },
    # The ServiceMonitor CRD is installed by (or already present with) the monitoring stack.
    opts=pulumi.ResourceOptions(depends_on=[
        monitoring.kube_prometheus_stack,
        frontend_metrics_service,
        redis_leader_metrics_service,
        redis_replica_metrics_service,
    ]),
)
