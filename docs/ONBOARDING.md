# Onboarding: the Kubernetes, AWS and networking you need to read SPEC.md

Read `CRASH-COURSE.md` first (45 min, diagrams), then this as the reference, then `DIAGRAMS.md`, `SPEC.md`, `DECISIONS.md`. Every concept below is tied to a concrete object in this repo so you can `kubectl get` it and see it.

Mental model in one line: **Kubernetes is a scheduler plus a database of desired state.** You write YAML saying "I want 4 copies of this container with these limits"; controllers inside the cluster loop forever making reality match. Everything else is vocabulary for the shapes that YAML can take.

---

## 1. The cluster

**Cluster** = a control plane (API server + etcd database + built-in controllers) and a set of **nodes** (VMs that run containers). On EKS, AWS runs the control plane for you ($0.10/hr) and you bring the nodes.

```
kubectl get nodes          # our 3 fixed m7g.large + whatever Karpenter has added
kubectl get pods -A        # every pod in every namespace
```

- **Node**: an EC2 instance running `kubelet` (talks to the API server, starts containers) and `kube-proxy` (programs Service routing). Ours: 3× `m7g.large` (2 vCPU, 8 GiB, Graviton arm64) in a *managed node group* (AWS manages the autoscaling group and AMI). Label `pr-runtime/pool=general`.
- **Namespace**: a folder for objects. Ours: `pr-runtime` (our app), `monitoring` (Prometheus/Grafana), `keda`, `kube-system` (cluster plumbing, Karpenter, CNI, DNS).
- **API server**: the only thing anyone talks to. `kubectl`, the controller pod, Karpenter, Helm — all just HTTP to the API server with a token. Our controller has `kubernetes` client code that lists and deletes pods through it (`controller/app/k8s.py`).

**Why arm64?** The laptop is Apple Silicon, the driver box is Graviton, the nodes are Graviton: one architecture end to end, no cross-compiling or emulation.

---

## 2. Pods and the things that make pods

**Pod** = one or more containers sharing a network namespace (one IP) and volumes. The smallest schedulable unit. Pods are cattle: they die, get replaced, get a new name and IP. You almost never create a pod directly.

**Deployment** → **ReplicaSet** → pods. You say `replicas: 4`; the ReplicaSet controller keeps 4 pods alive. Delete a pod, a new one appears. That is our **warm pool**: `deploy/chart/templates/runner.yaml` is a Deployment of N runner pods. The controller deletes a runner pod after it does one task; the ReplicaSet immediately creates a fresh one. Rolling updates: change the image tag, the Deployment creates new pods and kills old ones gradually.

- Gotcha we hit (DECISIONS): a Deployment pod has `restartPolicy: Always`. If the container *process exits*, kubelet restarts it **in the same pod** with the same emptyDir. "Exit after one task" therefore reused the dirty workdir. The fix: the controller deletes the pod via the API; deletion is the real ephemeral boundary.

**StatefulSet** = Deployment for things with identity and disk. Pods are named `name-0, name-1`, each gets its own PersistentVolumeClaim that follows it across restarts. Ours: `postgres-0` (`deploy/chart/templates/postgres.yaml`).

**DaemonSet** = one pod per node. Used for node-level agents: the VPC CNI (`aws-node`), `kube-proxy`, the Pod Identity agent, Prometheus node-exporter. You will see them in `kube-system`.

**Job** = run to completion, no restart on success. The KEDA baseline (`deploy/baseline/scaledjob.yaml`) creates one Job per SQS message. `kubectl get jobs -n pr-runtime`.

**Init container** = runs before the main containers, must succeed. The baseline uses one to pull the SQS message and write a task file, so the untrusted runner image never contains the AWS SDK.

### Pod spec fields you will see in our YAML

- `resources.requests` / `limits`: requests are what the **scheduler** uses to pick a node (sum of requests must fit); limits are enforced by cgroups (CPU throttled, memory OOM-killed). Runners: request 500m CPU / 512Mi, limit 1 CPU / 1Gi. "m" = millicores; 500m = half a core.
- `readinessProbe`: kubelet calls `GET /healthz` every 2s; while it fails, the pod is **NotReady** and removed from Services. We abuse this on purpose: a runner reports 200 only while idle, 503 once it has taken a task, so the controller (which reads Ready) never assigns twice.
- `securityContext`: `runAsNonRoot`, `readOnlyRootFilesystem`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`. Cheap hardening. Read-only root is why the runner copies its baked-in Flask clone from `/opt/seed` into a writable `emptyDir` at boot.
- `automountServiceAccountToken: false`: by default every pod gets a token to talk to the API server mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token`. Our runner gets none. Verify: `kubectl exec -n pr-runtime <runner> -- ls /var/run/secrets/kubernetes.io/` → nothing.
- `nodeSelector: pr-runtime/pool: general`: pin to the fixed nodes. Controller and Postgres have it; runners do not, so only runners float onto Karpenter nodes.
- `terminationGracePeriodSeconds`: how long between SIGTERM and SIGKILL on delete. Runners: 5s.

### Volumes

- **emptyDir**: scratch disk that lives as long as the pod. Runner `/work`, controller `/tmp` (git mirror lives there).
- **ConfigMap**: key/value or files, mounted as a directory or injected as env. We mount `eval-results` and `guidelines` ConfigMaps into the controller; Grafana's sidecar watches for ConfigMaps labeled `grafana_dashboard=1` and loads them as dashboards.
- **Secret**: same as ConfigMap, base64 (not encrypted by default; EKS encrypts etcd at rest). `pr-runtime-env` is created from `.env` by `scripts/sync_secret.sh`. Only Postgres mounts it now; the controller reads Secrets Manager instead.
- **PersistentVolumeClaim (PVC)** → **PersistentVolume** → EBS disk. The claim says "8Gi, class gp3"; the **CSI driver** (`aws-ebs-csi-driver` addon) creates the EBS volume and attaches it to whichever node the pod lands on. **StorageClass** `gp3` (we created it, default) tells the CSI driver what kind of disk. EBS volumes are AZ-bound: a pod with a PVC can only be rescheduled within that AZ.

---

## 3. Labels, selectors, and how things find each other

Everything is glued together by **labels** (key/value on objects) and **selectors** (queries over labels). No references by name.

- The runner Deployment's `selector: matchLabels: {app: runner}` owns pods labeled `app: runner`.
- The controller finds runners with `list_namespaced_pod(label_selector="app=runner")`.
- The NetworkPolicy's `podSelector: {app: runner}` applies to those pods; its `from: podSelector: {app: controller}` names who may talk to them.
- Prometheus's ServiceMonitor `selector: matchLabels: {app: controller}` finds the controller Service to scrape.
- Nodes carry labels too: `pr-runtime/pool=general|burst`, `karpenter.sh/capacity-type=spot`, `node.kubernetes.io/instance-type`.

```
kubectl get pods -n pr-runtime -l app=runner -o wide        # -o wide shows node and pod IP
kubectl get nodes -L pr-runtime/pool,karpenter.sh/capacity-type
```

---

## 4. Networking inside the cluster

### Pod IPs

On EKS with the **VPC CNI**, every pod gets a real VPC IP from the node's subnet (secondary IPs on the node's ENIs). So a pod IP like `10.0.12.110` is routable from anywhere in the VPC. This is how the controller talks to a runner: it reads the pod's IP from the API and does `POST http://10.0.12.110:8080/task`. No Service, no DNS, straight to the pod. Pod IPs change when pods are replaced, which is fine because the controller looks them up every tick.

### Services

A **Service** is a stable virtual IP + DNS name in front of a changing set of pods selected by label. `kube-proxy` on every node programs iptables/IPVS so that traffic to the Service IP is load-balanced to the backing pods that are **Ready**.

- `controller` Service (ClusterIP) → the controller pod. Runners POST results to `http://controller:8000/result/<id>`; the name resolves because of DNS (next). Within the namespace `controller` works; across namespaces it is `controller.pr-runtime.svc.cluster.local`.
- `postgres` is a **headless** Service (`clusterIP: None`): no virtual IP, DNS returns the pod IP directly. Standard for StatefulSets.
- Runners have **no Service**: the controller addresses them by pod IP on purpose (it needs a specific pod, not "any runner").

Service types: `ClusterIP` (internal only, ours), `NodePort` (opens a port on every node), `LoadBalancer` (AWS creates an NLB/ALB). We have no LoadBalancer Services: **nothing in the cluster is reachable from the internet.**

### DNS

**CoreDNS** runs as a Deployment in `kube-system`. Every pod's `/etc/resolv.conf` points at the `kube-dns` Service. Names: `<service>.<namespace>.svc.cluster.local`, with search domains so `controller` alone works inside the `pr-runtime` namespace. External names (`github.com`, `api.openai.com`) are forwarded to the VPC resolver. This is why the NetworkPolicy must allow UDP/TCP 53 to `kube-dns`, or nothing resolves.

### NetworkPolicy

By default every pod can talk to every pod and to the internet. A **NetworkPolicy** is a firewall rule set selected by pod labels; it needs a CNI that enforces it (we turned on `enableNetworkPolicy` in the VPC CNI addon, which runs an eBPF node agent). Ours (`deploy/chart/templates/networkpolicy.yaml`) applies to runners:
- **Ingress**: only from pods labeled `app: controller`, port 8080. (Kubelet health probes bypass NetworkPolicy.)
- **Egress**: to the controller on 8000; to `kube-dns` on 53; to `0.0.0.0/0` port 443 **except** `10.0.0.0/8` (the VPC) and `169.254.169.254/32` (the EC2 metadata service, which hands out node credentials).

Verified from inside a runner: `github.com:443` opens, `example.com:80`, `postgres:5432` and the metadata IP all time out. The honest hole: any host on 443 is reachable, because GitHub's IP ranges are not something we pin. A proxy for clones would close it.

### Egress to the internet

Nodes live in **private subnets** (no public IP). Outbound traffic (image pulls, GitHub clones, OpenAI calls, SQS) goes through a **NAT gateway** in a public subnet. Inbound from the internet to a private subnet is impossible by construction. One NAT for the whole VPC ($0.045/hr + per-GB); a production setup would run one per AZ.

### Getting to things from your laptop

There is no public endpoint for the console or Grafana. You use **`kubectl port-forward`**: `kubectl` opens an authenticated tunnel through the API server to a pod/Service and binds a local port.

```
kubectl port-forward -n pr-runtime svc/controller 18000:8000      # console at http://localhost:18000
kubectl port-forward -n monitoring svc/kps-grafana 13000:80       # Grafana at http://localhost:13000
```

It dies when the target pod is replaced (every controller rollout) or your laptop sleeps. Several of our overnight script hangs were a dead port-forward, nothing more.

### The EKS API endpoint itself

The API server has a public DNS name. We left `endpoint_public_access = true` so `kubectl` works from the laptop (authenticated by IAM, so "public" means reachable, not open). Inside the VPC the same name resolves to **private IPs** of the control plane ENIs, and those ENIs sit behind the **cluster security group**. That is why the driver box timed out until we added a 443 ingress rule from its security group (`infra/driver.tf`).

---

## 5. Networking outside the cluster (AWS)

- **VPC** `10.0.0.0/16`, two AZs, each with a private subnet (nodes, driver, pods) and a public subnet (NAT gateway). `infra/vpc.tf`.
- **Security groups** = stateful per-ENI firewalls. Ours: cluster SG (API server ENIs), node SG (nodes; the EKS module adds node↔node and node↔control-plane rules), driver SG (egress all), and the Karpenter nodes reuse the node SG via a `karpenter.sh/discovery` tag. Rule descriptions may not contain `>` (we learned).
- **The webhook path**: GitHub (internet) → **API Gateway HTTP API** (AWS-managed public endpoint) → **Lambda** (`infra/lambda/ingress.py`, verifies the HMAC with the webhook secret, drops pings) → **SQS** queue. The controller long-polls SQS from inside the VPC via NAT. Nothing public touches the cluster. The original plan (API GW → SQS directly) failed because that integration cannot copy a header into a message attribute.
- **SQS** = durable queue; a message that is received but not deleted within the visibility timeout (10 min) reappears; after 3 receives it goes to the dead-letter queue. The controller deletes a message as soon as it has written the task row to Postgres.
- **ECR** = container registry. Three repos: `pr-runtime/controller`, `pr-runtime/runner`, `pr-runtime/tools`. Nodes pull with their node IAM role; the driver pushes with its instance role.
- **Secrets Manager** `pr-runtime/app` = one JSON secret with the OpenAI key, GitHub token, Postgres password, judge model. Read by the controller at boot and by the driver's `driver_env.sh`.
- **SSM Session Manager / Run Command** = how we reach the driver box without SSH keys or an open port 22. `scripts/driver.sh` wraps `aws ssm send-command`.

---

## 6. Identity: who is allowed to do what

Two separate systems, both involved.

**Kubernetes RBAC** (inside the cluster): a **ServiceAccount** is a pod's identity; **Roles** grant verbs on resources; **RoleBindings** connect them. Our controller's ServiceAccount `controller` has a Role allowing `get/list/watch/delete` on pods in the `pr-runtime` namespace, which is exactly what the scheduler loop does. Runners have no ServiceAccount token at all.

**AWS IAM** (outside): a pod that calls AWS (SQS, Secrets Manager, EC2 for Karpenter) needs AWS credentials. Two mechanisms:
- **IRSA** (older): annotate the ServiceAccount with a role ARN; the pod gets a projected OIDC token it exchanges for AWS creds. Needs an OIDC provider per cluster.
- **EKS Pod Identity** (what we use): an `aws_eks_pod_identity_association` says "ServiceAccount X in namespace Y may assume role Z"; a node agent hands credentials to the pod. Ours: controller → SQS + Secrets Manager; Karpenter → EC2; EBS CSI → EBS; KEDA → SQS queue depth; baseline job → SQS receive.

**Who may use `kubectl`**: EKS **access entries** map IAM principals to Kubernetes permissions. The cluster creator (your root user) got admin automatically; the driver box's instance role got an access entry + `AmazonEKSClusterAdminPolicy` in Terraform. `aws eks update-kubeconfig` writes a kubeconfig whose token is minted from your AWS credentials.

---

## 7. Helm, operators, CRDs

**Helm** = templated YAML + a release record. `helm upgrade --install pr-runtime deploy/chart -n pr-runtime` renders `deploy/chart/templates/*.yaml` with `values.yaml` and applies the result; `helm template` shows what it would apply. Our chart is small on purpose: controller, runners, NetworkPolicy, Postgres. Third-party charts we installed: `kube-prometheus-stack`, `keda`, `karpenter`.

**CRD** (Custom Resource Definition) = a new object type added to the API server, usually shipped with an **operator** (a controller that acts on it). You will see:
- `ServiceMonitor` (Prometheus operator): "scrape this Service's `/metrics` every 5s."
- `ScaledJob`, `TriggerAuthentication` (KEDA): "spawn Jobs based on SQS queue depth."
- `NodePool`, `EC2NodeClass`, `NodeClaim` (Karpenter): "when pods are unschedulable, launch an EC2 instance matching these constraints; remove it when idle."
- `PolicyEndpoint` (VPC CNI): the compiled form of our NetworkPolicy.

```
kubectl get crd | head
kubectl get nodeclaims; kubectl get nodepool
```

---

## 8. Scheduling and autoscaling, as it applies to us

The **kube-scheduler** places each new pod on a node with enough unreserved *requests*, honoring `nodeSelector`, taints, and affinities. If no node fits, the pod sits **Pending**.

**Karpenter** watches Pending pods, computes the cheapest instance type that fits them (within the NodePool's constraints: arm64, listed families and sizes, spot or on-demand), launches it, and labels it so the pods land there. When a node is empty or underutilized for `consolidateAfter: 60s`, it drains and terminates it. It also watches an SQS **interruption queue** fed by EventBridge for spot reclaim notices (2-minute warning) and drains proactively.

Our two layers of "scheduling" are different things and it helps to keep them apart:
- **Kubernetes** schedules *pods onto nodes* (CPU/memory). Karpenter adds nodes when that fails.
- **Our controller** schedules *tasks onto runner pods* (admission cap, diff-size ranking, warm pool). It never talks to nodes.

`kubectl drain <node>` evicts pods from a node (what a spot interruption or a node upgrade does). Our drain test proved a busy runner's task gets requeued immediately because the controller notices the pod is gone.

---

## 9. Observability plumbing

- The controller exposes Prometheus text format at `/metrics` (`prometheus_client`). A `ServiceMonitor` tells the Prometheus operator to scrape it; Prometheus stores time series; Grafana queries Prometheus with PromQL.
- PromQL you will see in `dashboards/pr-runtime.json`: `max(prr_tasks_pending)` (gauge, collapsed across controller restarts), `sum(rate(prr_tasks_total[1m]))` (counter → per-second rate), `histogram_quantile(0.95, sum(rate(prr_time_to_comment_seconds_bucket[2m])) by (le))` (p95 from a histogram).
- `kube-state-metrics` turns Kubernetes objects into metrics (`kube_pod_status_phase`), which is how the dashboard counts runner pods.
- Logs: `kubectl logs -n pr-runtime deploy/controller --since=10m`. No log aggregation; not needed at this size.

---

## 10. Reading our system with this vocabulary

1. GitHub webhook → API Gateway → Lambda (HMAC) → SQS. Public edge is all AWS-managed; the cluster has no public surface.
2. Controller pod (Deployment, 1 replica, pinned to fixed nodes, Pod Identity for SQS + Secrets Manager, RBAC to list/delete pods) consumes SQS, writes Postgres (StatefulSet, EBS PVC), and keeps an in-memory priority queue.
3. Runner pods (Deployment, N replicas, no SA token, read-only root, NetworkPolicy) sit Ready. The controller POSTs to a pod IP; the runner clones via NAT, runs tests, POSTs back via the `controller` Service, goes NotReady; the controller deletes it; the ReplicaSet makes a new one.
4. Burst: pods beyond the floor's capacity go Pending; Karpenter launches a spot Graviton node (~40s); consolidation removes it a minute after the pool shrinks.
5. Everything you look at from a laptop goes through `kubectl port-forward`; everything heavy (image builds, evals) runs on the driver box over SSM.

---

## 11. Commands you should be able to run and explain

```bash
# cluster state
kubectl get nodes -L pr-runtime/pool,karpenter.sh/capacity-type
kubectl get pods -n pr-runtime -o wide
kubectl describe pod -n pr-runtime <runner-pod>          # events, probes, volumes, limits
kubectl get networkpolicy -n pr-runtime -o yaml
kubectl get pvc,sc -n pr-runtime
kubectl get servicemonitor,nodepool,nodeclaims -A

# behavior
kubectl logs -n pr-runtime deploy/controller --since=5m
kubectl scale deploy runner -n pr-runtime --replicas=16   # watch Karpenter: kubectl get nodeclaims -w
kubectl exec -n pr-runtime <runner-pod> -- python -c "import socket; socket.create_connection(('github.com',443),timeout=5); print('ok')"
kubectl port-forward -n pr-runtime svc/controller 18000:8000

# heavy work, on the driver box
scripts/driver.sh 'cd ~/pr-runtime && git pull && make build-controller push rollout'
```

## 12. Glossary

| Term | One line |
|---|---|
| Pod | Smallest unit; one IP; one or more containers. |
| Deployment / ReplicaSet | Keep N identical pods alive; rolling updates. Our warm pool. |
| StatefulSet | Pods with stable names and their own disks. Postgres. |
| DaemonSet | One pod per node. CNI, kube-proxy, node agents. |
| Job | Run to completion. KEDA baseline. |
| Service | Stable IP/DNS over a label-selected set of Ready pods. `controller`, `postgres`. |
| Readiness probe | Pod is "Ready" only while the probe passes; we use it as an idle flag. |
| emptyDir / ConfigMap / Secret / PVC | Scratch disk / config files / sensitive config / persistent disk. |
| StorageClass / CSI | How a PVC becomes an EBS volume. `gp3`. |
| Namespace | Folder. `pr-runtime`, `monitoring`, `kube-system`. |
| Label / selector | The glue; everything finds everything by labels. |
| NetworkPolicy | Pod firewall; needs CNI enforcement (VPC CNI eBPF agent). |
| CoreDNS | Cluster DNS; `svc.namespace.svc.cluster.local`. |
| VPC CNI | Pods get real VPC IPs from the node's subnet. |
| NAT gateway | Private subnets' only way out to the internet. |
| Security group | AWS stateful firewall on an ENI; cluster SG guards the API endpoint. |
| ServiceAccount / Role / RoleBinding | Kubernetes identity and permissions for pods. |
| Pod Identity / IRSA | How a pod gets AWS credentials. We use Pod Identity. |
| Access entry | How an IAM principal gets `kubectl` rights. |
| Helm chart / release | Templated YAML and its install record. |
| CRD / operator | New object type plus the controller that acts on it. Karpenter, KEDA, Prometheus operator. |
| Karpenter NodePool / NodeClaim | Node autoscaling rules / one provisioned node. |
| Drain / cordon | Evict pods from a node / stop scheduling onto it. |
| port-forward | Authenticated tunnel from your laptop to a pod or Service through the API server. |
| ServiceMonitor / PromQL | Scrape config for Prometheus / its query language. |
| SQS visibility timeout / DLQ | Redelivery window for unacked messages / where poison messages end up. |
| SSM Run Command | Run shell on an EC2 instance without SSH. `scripts/driver.sh`. |
