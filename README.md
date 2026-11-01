# Deploy the Kubernetes Guestbook App (Python)

Based on the Pulumi tutorial: https://www.pulumi.com/dev/tutorials/guestbook/

The Guestbook is a multi-tier web app (Redis + PHP frontend). You will stand up a Redis leader,
Redis replicas, and the Guestbook frontend, expose and view the frontend service, perform an
incremental update that scales the frontend, and then clean everything up.

**One change from the tutorial:** every resource is created in its own namespace (`guestbook` by
default) so it cannot conflict with anything else on your cluster.

## Accessing the apps: LoadBalancer, no port-forwarding needed
This deployment is meant for a cluster that can hand out LoadBalancer addresses (a cloud provider, or
MetalLB on bare metal), so **no `kubectl port-forward` step is required**:

| App | How it is exposed | Where to find the address |
|---|---|---|
| Guestbook frontend | `LoadBalancer` when `pulumi config set useLoadBalancer true` | `pulumi stack output frontend_ip` |
| Grafana | `LoadBalancer` by default (`grafanaServiceType`) | `pulumi stack output grafana_url` |
| Prometheus | internal `ClusterIP` only (its UI has no login, so it is not published) | verified through Grafana, see Step 3 |

`kubectl port-forward` is only a fallback: for a cluster without a load-balancer provider, or to open
Prometheus's own Targets page. Those commands are marked **optional** below.

## Objectives
- Start up a Redis leader
- Start up Redis replicas
- Start up the Guestbook frontend
- Expose and view the frontend service
- Clean up

## Before you begin
1. [Install Pulumi](https://www.pulumi.com/docs/install/)
2. [Connect Pulumi to a Kubernetes cluster](https://www.pulumi.com/registry/packages/kubernetes/installation-configuration/)
   (i.e. `kubectl config current-context` points at your cluster)
3. Python 3.9+ and `kubectl`
4. A Pulumi backend: a Pulumi Cloud account, or `pulumi login --local` for no account

## Create and configure a project
Using the template, as in the tutorial:
```bash
mkdir k8s-guestbook && cd k8s-guestbook
pulumi new kubernetes-python
```
Then replace the template's `__main__.py` with the one in this repo.

(Or skip the template and use this repo directly: `pulumi stack init dev`.)

The program has three tiers: a Redis leader, Redis replicas, and the frontend, each a Deployment
plus a Service. The frontend Service is `ClusterIP` by default, which works on Minikube and similar
clusters. If your cluster can allocate load balancers (cloud, or MetalLB on bare metal), run:
```bash
pulumi config set useLoadBalancer true
```
If you're not sure, skip this step.

Optional: use a different namespace than `guestbook`:
```bash
pulumi config set namespace my-namespace
```

## Deploying
```bash
pulumi up
```
Pulumi shows a preview of the resources to create (the namespace, 3 Deployments, 3 Services),
then asks for confirmation. Select **yes**. Expected output ends with:
```
frontend_ip: "10.x.x.x"
namespace:   "guestbook"
8 resources created
```

## Viewing the Guestbook
Check the deployed services (note the `-n guestbook`):
```bash
kubectl -n guestbook get services
```
You should see `frontend`, `redis-leader` and `redis-replica`. Also check the pods:
```bash
kubectl -n guestbook get pods
```
Print the exported value:
```bash
pulumi stack output frontend_ip
```
It matches the `frontend` CLUSTER-IP (or EXTERNAL-IP if `useLoadBalancer` is `true`).

**With a LoadBalancer** (`useLoadBalancer true`, the setup used here): no port-forward needed.
```bash
curl $(pulumi stack output frontend_ip)
```
The Guestbook HTML is returned (`<html ng-app="redis"> ... <title>Guestbook</title>`).
Open `http://<frontend_ip>` in a browser, add a few entries, and refresh to see them persist.

**Without a LoadBalancer** (ClusterIP, optional fallback): forward a local port instead.
```bash
kubectl -n guestbook port-forward svc/frontend 8765:80
curl localhost:8765        # and open http://localhost:8765 in a browser
```

## Making an incremental update
Scale the frontend from 3 replicas to 5. In `__main__.py`, in the `frontend` Deployment, change:
```python
"replicas": 3,
```
to:
```python
"replicas": 5,
```
Or: `sed -i "s/\"replicas\": 3/\"replicas\": 5/g" __main__.py`

Then:
```bash
pulumi up -y --skip-preview
```
Pulumi works out the minimal change: only the `frontend` Deployment is updated; the rest is unchanged
(the preview/summary shows `~ 1 updated` and the other resources unchanged).
```bash
kubectl -n guestbook get pods      # 5 frontend pods, each 2/2 (app + apache-exporter sidecar)
```
With the monitoring from steps 2-3 deployed, the new pods are scraped automatically: after about a
minute, `up{namespace="guestbook"}` in Grafana Explore returns 7 series (5 frontend + redis-leader +
redis-replica), and the dashboard's "Scrape targets UP" tile shows 7.

To scale back: `sed -i "s/\"replicas\": 5/\"replicas\": 3/g" __main__.py && pulumi up -y --skip-preview`

## Step 2: Prometheus and Grafana
`monitoring.py` (imported at the bottom of `__main__.py`) deploys the
[kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
Helm chart into its own `guestbook-monitoring` namespace: Prometheus Operator (with the `ServiceMonitor` CRDs),
Prometheus, Grafana (Prometheus pre-configured as a datasource), node-exporter and kube-state-metrics.

```bash
pulumi up
kubectl -n guestbook-monitoring get pods,svc
```
Wait until all pods are `Running` (first install pulls several images, 2-5 minutes).

Grafana is exposed as a `LoadBalancer` (set `grafanaServiceType` to `NodePort` if your cluster has no
load-balancer provider). Access details:
```bash
pulumi stack output grafana_url
pulumi stack output grafana_admin_user
pulumi stack output grafana_admin_password    # add --show-secrets if you set a custom password
```
Default login is `admin` / `prom-operator`. Open the `grafana_url` address directly: no port-forward needed.

Optional fallbacks (only if your cluster has no load-balancer provider, or to see Prometheus's own UI,
which is deliberately not exposed publicly):
```bash
# Prometheus: http://localhost:9090  (Status > Targets)
kubectl -n guestbook-monitoring port-forward svc/gb-monitoring-prometheus 9090:9090

# Grafana, only if grafana_url shows no LoadBalancer address: http://localhost:3000
kubectl -n guestbook-monitoring port-forward svc/gb-grafana 3000:80
```
Config (optional): `monitoringNamespace` (default `guestbook-monitoring`), `kubePrometheusStackVersion`,
`reuseExistingOperator`, `grafanaServiceType`, `grafanaAdminPassword`.

### Cluster already runs a Prometheus Operator?
CRDs and the operator are cluster-wide, so a second full stack conflicts with an existing one
(`... exists and cannot be imported into the current release`). In that case:
```bash
helm list -A | grep kube-prometheus-stack        # note the CHART version of the existing release
pulumi config set kubePrometheusStackVersion <that chart version>
pulumi config set reuseExistingOperator true
pulumi up
```
This deploys only our own Prometheus + Grafana (in `guestbook-monitoring`) and lets the existing
operator reconcile them. If no `prometheus-gb-monitoring-prometheus-0` pod appears, the existing
operator is restricted to certain namespaces and must be allowed to watch `guestbook-monitoring`.

## Step 3: Guestbook metrics scraped by Prometheus
The stock Guestbook images expose no Prometheus metrics, so each pod gets an exporter sidecar
(containers in a pod share `localhost`):

| Tier | Sidecar | Port | Example metrics |
|---|---|---|---|
| frontend (Apache/PHP) | `apache-exporter` (reads Apache `mod_status`) | 9117 | `apache_accesses_total`, `apache_workers`, `apache_up` |
| redis-leader / redis-replica | `redis_exporter` | 9121 | `redis_commands_processed_total`, `redis_connected_clients`, `redis_master_link_up` |

Three internal ClusterIP Services (`frontend-metrics`, `redis-leader-metrics`, `redis-replica-metrics`,
all labelled `metrics=true`) expose only the exporter ports, so metrics are not published on the
frontend's public LoadBalancer. One `ServiceMonitor` (`guestbook`) selects them and Prometheus scrapes
every 15s. Pod CPU/memory comes from the kubelet's cAdvisor metrics, no sidecar needed.

Config: `redisImage` (default `redis:6.2`), `redisExporterImage`, `apacheExporterImage`.

```bash
pulumi up
kubectl -n guestbook get pods          # frontend pods 2/2, redis pods 2/2
```
Note: redeploying the Redis pods clears any entries (no persistent volume).

### Verify that Guestbook metrics are scraped by Prometheus
No port-forward needed: use Grafana through its LoadBalancer.
1. Open Grafana (`pulumi stack output grafana_url`) > **Explore**, choose the **Prometheus** datasource and
   run `up{namespace="guestbook"}`. Expect 5 series (3 frontend, 1 redis-leader, 1 redis-replica), all
   with value `1`. The **Guestbook** dashboard's "Scrape targets UP" tile shows the same count.
2. Optional, Prometheus's own Targets page (Prometheus is internal-only, so this one uses port-forward):
   ```bash
   kubectl -n guestbook-monitoring port-forward svc/gb-monitoring-prometheus 9090:9090
   ```
   Then open http://localhost:9090/targets and look for `serviceMonitor/guestbook/guestbook/0`: 5
   endpoints, all **UP**.
3. Generate traffic and watch the request counter move:
   ```bash
   for i in $(seq 200); do curl -s http://$(pulumi stack output frontend_ip)/ > /dev/null; done
   ```
   `sum(rate(apache_accesses_total[1m]))` (requests/s; includes the exporter's own scrapes of
   `/server-status`), `redis_commands_processed_total`, `redis_connected_clients`.
4. Resource usage: `sum by (pod) (container_memory_working_set_bytes{namespace="guestbook", container!=""})`
   and `sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="guestbook", container!=""}[5m]))`.

## Stretch goal: Guestbook Grafana dashboard
A **Guestbook** dashboard is provisioned from code (`guestbook_dashboard.py`, loaded through a
ConfigMap labelled `grafana_dashboard=1`, which Grafana's dashboard sidecar picks up automatically).
Open Grafana (`pulumi stack output grafana_url`) > **Dashboards > Guestbook**.

| Row | Panels |
|---|---|
| Health | scrape targets UP (expect 5), frontend requests/s, Apache up, Redis replica linked to leader |
| Frontend | request rate per pod, Apache busy vs idle workers |
| Redis | commands/s, connected clients, memory used, rejected connections (errors) |
| Resources | pod CPU (cores) and memory (working set) for every Guestbook pod |

Generate traffic to see the graphs move:
```bash
for i in $(seq 500); do curl -s http://$(pulumi stack output frontend_ip)/ > /dev/null; done
```
Apache's status page reports no HTTP error codes, so "errors" are shown as Redis rejected
connections plus the up/down indicators for the exporters and the replica link.

## Cleaning up
```bash
pulumi destroy --yes
pulumi stack rm --yes
```
`destroy` also deletes the `guestbook` namespace. Verify:
```bash
kubectl get ns guestbook      # Error from server (NotFound)
```
