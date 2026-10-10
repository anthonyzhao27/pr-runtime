# Kubernetes crash course

General Kubernetes, one running example, ASCII diagrams. About an hour. Lines starting with **In pr-runtime:** are one-line pointers to our project; skip them on a first pass. The project-specific walkthrough is `PROJECT-WALKTHROUGH.md`.

**Running example:** a FastAPI shop backend with a Postgres database. Users call `POST /orders`.

---

## 1. What Kubernetes is for

You have containers and many machines. You want:
- "run 3 copies of this container, somewhere"
- "if one dies, replace it"
- "give them a stable address"
- "roll out a new version without downtime"
- "put them in the right place given CPU/memory"

You do not want to pick machines, restart crashes, or update load balancers by hand.

The core idea: **you declare the desired state; Kubernetes continuously makes reality match it.**

```
  you:  "I want 3 copies of shop-backend:v2"          (desired state, YAML)
              │
              ▼
        ┌───────────┐   compare   ┌──────────────────┐
        │ desired   │◄───────────►│ actual: 2 running │
        └───────────┘             └──────────────────┘
              │ difference: 1 missing
              ▼
        controller starts 1 more        ← this loop runs forever
```

Everything in Kubernetes is an instance of this loop. If you remember one thing, remember this.

---

## 2. Cluster architecture

A cluster = a **control plane** (the brain) + **worker nodes** (machines that run your containers).

```
┌───────────────────── CONTROL PLANE (managed by AWS/GCP on EKS/GKE) ─────────────────────┐
│                                                                                          │
│   kube-apiserver  ◄───────── the hub. EVERYTHING talks to this, nothing talks around it  │
│       ▲   ▲   ▲                                                                          │
│       │   │   └── etcd              database of all objects (the source of truth)        │
│       │   └────── scheduler         decides which node each new pod runs on              │
│       └────────── controller-manager the reconcile loops (Deployment, ReplicaSet, Job…)  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ watch / report                                  ▲ kubectl, helm, CI
        │                                                 │
┌───────┴──────────── WORKER NODE (a VM) ─────────────┐   you
│  kubelet        agent: runs the pods assigned to    │
│                 this node, runs health checks,      │
│                 reports status                      │
│  container      containerd: pulls images, runs      │
│  runtime        containers                          │
│  kube-proxy     programs Service routing rules      │
│  CNI plugin     gives pods IPs, enforces network    │
│                 policy                              │
│  [pod] [pod] [pod]                                  │
└─────────────────────────────────────────────────────┘
```

Rules to internalize:
- **The API server is the only hub.** Scheduler, controller-manager, kubelets, kubectl, ingress controllers all *watch* it and *write* to it. Nothing calls kubelet directly (except the API server proxying `exec`/`logs`).
- **kubelet decides nothing.** It is told "run pod X on this node" and does it.
- **Scheduler decides where. Controllers decide how many.**
- Your application pods usually never talk to the API server. They just serve traffic.

### What happens when you create a Deployment

```
 kubectl apply deployment.yaml
        │
        ▼
 API server stores it ──► controller-manager notices: "Deployment wants 3, has 0"
                                │ creates a ReplicaSet, which creates 3 Pod objects (unassigned)
                                ▼
                          scheduler notices unassigned pods
                                │ picks nodes by CPU/memory fit and rules, writes nodeName
                                ▼
                          kubelet on each chosen node notices "a pod is for me"
                                │ pulls the image, starts the container, runs probes
                                ▼
                          kubelet reports Running ──► API server ──► you see it in kubectl
```

---

## 3. The workload objects, bottom to top

```
 Container   one process in an image
    └─ Pod            1+ containers sharing an IP and volumes. Smallest deployable unit. Disposable.
        └─ ReplicaSet    "keep exactly N identical pods alive"
            └─ Deployment   ReplicaSet + rolling updates and rollback.   ← what you write for stateless apps
```

**Pod.** Gets its own IP. When it dies it is gone; a replacement gets a *new* name and a *new* IP. You almost never create bare pods.

**Deployment.** For stateless services (our FastAPI backend). Change the image tag → it creates a new ReplicaSet, scales it up while scaling the old one down.

```
 rolling update v1 → v2 (3 replicas, maxSurge 1, maxUnavailable 0)
   v1 v1 v1
   v1 v1 v1 v2        start one v2, wait until Ready
   v1 v1 v2 v2        kill one v1
   v1 v2 v2 v2 … v2 v2 v2     never fewer than 3 serving
 kubectl rollout undo deployment/shop   → back to v1
```

**StatefulSet.** For things with identity and disk: databases, Kafka. Pods are named `postgres-0, postgres-1`, start in order, and each keeps its own persistent volume across restarts.

**DaemonSet.** One pod per node. Log shippers, node monitoring, the network plugin.

**Job / CronJob.** Run to completion once / on a schedule. Batch work.

| You want | Use |
|---|---|
| stateless web service | Deployment |
| database, anything needing stable identity + disk | StatefulSet |
| an agent on every node | DaemonSet |
| run a task and finish | Job |
| a task every night | CronJob |

**In pr-runtime:** the runner pool is a Deployment (warm pool), Postgres is a StatefulSet, the CNI and kube-proxy are DaemonSets, the KEDA baseline used Jobs.

---

## 4. Labels and selectors: how objects find each other

Kubernetes objects don't point at each other by name. They carry **labels** (key/value tags) and other objects select them with **selectors** (queries).

```
 Deployment shop               Service shop
   selector: app=shop            selector: app=shop
   creates pods labeled app=shop ──────► "route to every Ready pod labeled app=shop"
```

This one mechanism links Deployments to pods, Services to pods, NetworkPolicies to pods, and nodes to scheduling rules. Typo in a label = a Service with zero backends = "connection refused." It is the single most common beginner bug.

```
 kubectl get pods -l app=shop            # query by label
 kubectl get endpoints shop              # empty list = selector matches no Ready pods
```

---

## 5. Networking

The model Kubernetes promises (the CNI plugin delivers it):

1. Every pod gets its own IP.
2. Every pod can reach every other pod by IP, no NAT in between.
3. Agents on a node can reach all pods on that node.

### 5a. Pod IPs are ephemeral, so Services exist

```
  pod shop-abc   10.0.1.5      ← will die and be replaced
  pod shop-def   10.0.2.9      ← by pods with different IPs
  pod shop-ghi   10.0.1.7
```

A **Service** is a stable name and virtual IP in front of a changing set of pods:

```
  client pod ── http://shop ──►  DNS: shop → 172.20.45.10   (virtual IP, owned by nobody)
                                          │
                                 kube-proxy rule on every node:
                                 "172.20.45.10:80 → one of the READY pod IPs"
                                          │
                       ┌──────────────────┼──────────────────┐
                       ▼                  ▼                  ▼
                   10.0.1.5:8000      10.0.2.9:8000      10.0.1.7:8000
```

- A Service is **not a process**. It is an API object that two things turn into plumbing: **CoreDNS** (name → virtual IP) and **kube-proxy** (virtual IP → real pod IP, via iptables/IPVS rules in each node's kernel).
- Only **Ready** pods (readiness probe passing) are in the rotation.
- Selector decides membership; `port` is what clients hit; `targetPort` is the container's port.

Service types:

| Type | Reachable from | Use |
|---|---|---|
| ClusterIP (default) | inside the cluster only | service-to-service |
| NodePort | any node's IP + high port | rarely directly |
| LoadBalancer | the internet (cloud creates an LB) | exposing one service |
| Headless (`clusterIP: None`) | DNS returns pod IPs directly | StatefulSets, databases |

### 5b. DNS

Every Service gets a name: `shop.default.svc.cluster.local`. Inside the same namespace, `shop` is enough. CoreDNS (a Deployment in `kube-system`) answers. If DNS is broken, everything looks broken; test with `nslookup shop` first.

### 5c. Exposing to the internet: LoadBalancer vs Ingress

```
  user ── POST /orders ──► cloud Load Balancer ──► ingress controller pods (nginx) ──► Service ──► pod
        (public IP, L4)        created by a            (L7: reads Host + path)        (kube-proxy)
                               Service type=LoadBalancer
```

- **Ingress** = a routing-table object ("host `api.shop.com`, path `/orders` → Service `shop`").
- **Ingress controller** = the pods that implement it (nginx, AWS Load Balancer Controller, Traefik). They watch Ingress objects and reconfigure themselves. Without a controller, an Ingress does nothing.
- The cloud load balancer exists because only the cloud can hand out a public IP.
- Layers: L4 balancers see TCP only; L7 (nginx, ALB) understand HTTP paths, hosts, headers.

The control plane is **not** in this request path. It set things up beforehand and only reacts to changes.

### 5d. NetworkPolicy: pod-level firewall

Default: all pods can talk to all pods and out to the internet. A **NetworkPolicy** selects pods by label and allows only listed traffic, in and/or out. It needs a CNI that enforces it (Calico, Cilium, AWS VPC CNI with the option on).

```
 policy on app=postgres:  ingress allowed only from app=shop on :5432
 → a compromised "frontend" pod cannot connect to the database
```

**In pr-runtime:** runners get an egress allow-list; there is no Ingress at all because nothing is public (work arrives by pulling from a queue). One Service (`controller`) lets runners report back by name.

---

## 6. Config, secrets, storage

| Need | Object | How the pod gets it |
|---|---|---|
| non-secret settings | **ConfigMap** | env vars or files in a mounted directory |
| passwords, tokens | **Secret** | env vars or mounted files (base64, not encrypted by default; enable etcd encryption) |
| scratch space | **emptyDir** volume | lives and dies with the pod |
| data that must survive | **PersistentVolumeClaim** | a disk mounted into the pod |

### Persistent storage chain

```
  StatefulSet postgres
        │  asks for storage
        ▼
  PVC  "8Gi, class gp3"        (the claim: what I need)
        │  StorageClass says which driver provisions it
        ▼
  CSI driver (aws-ebs-csi)     creates a real EBS volume, attaches it to the node
        ▼
  PersistentVolume             the real disk, bound to the claim
        │  mounted into the pod at /var/lib/postgresql/data
```

Cloud block disks are tied to one availability zone, so a pod with an EBS volume can only reschedule within that zone.

---

## 7. Scheduling and resources

The scheduler places each pod on a node that has room and satisfies the pod's rules.

**Requests vs limits**

```
 resources:
   requests: { cpu: 500m, memory: 512Mi }   ← scheduler RESERVES this when placing the pod
   limits:   { cpu: "1",  memory: 1Gi }     ← kernel ENFORCES this at runtime
```
- 1000m = 1 CPU core.
- Over the CPU limit: process is throttled. Over the memory limit: killed (`OOMKilled`).
- The scheduler only looks at **requests**. If no node has enough unreserved requests, the pod stays `Pending`.

**Steering placement**

| Mechanism | Meaning |
|---|---|
| `nodeSelector` | only nodes with this label |
| node affinity | same, with soft preferences |
| pod (anti-)affinity | put near / away from other pods, e.g. spread replicas across zones |
| taints + tolerations | node says "keep out unless you tolerate me" (GPU nodes, dedicated pools) |
| topology spread | even out pods across zones/nodes |

**Quality of service** (derived from requests/limits): Guaranteed (requests = limits) is evicted last under node memory pressure; BestEffort (none set) first.

---

## 8. Health, rollouts, self-healing

Three probes the kubelet runs against each container:

| Probe | Question | On failure |
|---|---|---|
| **startup** | has the app finished booting? | keep waiting, then restart |
| **liveness** | is it stuck? | **restart the container** |
| **readiness** | can it take traffic right now? | **remove from Service endpoints**, no restart |

Readiness is what makes rolling updates safe: a new pod gets traffic only once it passes.

Common pod states and what they mean:

| State | Usually means |
|---|---|
| `Pending` | no node fits (requests too big, selector/taint mismatch), or waiting on a PVC |
| `ContainerCreating` | pulling image, mounting volumes |
| `ImagePullBackOff` | wrong image name/tag or no registry permission |
| `CrashLoopBackOff` | container starts then exits repeatedly; read `kubectl logs --previous` |
| `OOMKilled` (in describe) | hit the memory limit |
| `Running` but not `Ready` | readiness probe failing |
| `Terminating` (stuck) | finalizer or node unreachable |

---

## 9. Scaling

Three different layers. Do not confuse them.

```
  more traffic ──► HPA            changes the NUMBER OF PODS from CPU/memory/custom metrics
                    │
                    ▼  new pods are Pending: no room
                  Cluster autoscaler / Karpenter     adds NODES so the pods can be scheduled
                    │
                    ▼  load drops
                  both scale back down; empty nodes get removed
```

| Tool | Scales | Trigger |
|---|---|---|
| HPA (Horizontal Pod Autoscaler) | pods | CPU, memory, custom metrics |
| VPA | a pod's requests/limits | observed usage |
| KEDA | pods (incl. to zero) / Jobs | external events: queue depth, Kafka lag, cron |
| Cluster Autoscaler / Karpenter | **nodes** | Pending pods |

Karpenter vs Cluster Autoscaler: Cluster Autoscaler resizes pre-defined node groups; Karpenter looks at the pending pods and launches whichever instance type fits cheapest, and removes idle nodes.

**In pr-runtime:** Karpenter provides burst capacity (spot nodes in ~40s); KEDA was only the cold-start baseline; our own controller schedules *tasks* onto pods, a separate layer on top of all of this.

---

## 10. Identity and security

Two separate questions:

```
  (A) who may call the Kubernetes API?        → RBAC
  (B) who may call the CLOUD (S3, SQS, …)?    → cloud IAM, wired to pods
```

**RBAC:** `ServiceAccount` (a pod's identity) + `Role` (verbs on resources in a namespace; `ClusterRole` is cluster-wide) + `RoleBinding` (connects them).
```
  Role "pod-reader":  get, list, watch  on pods
  RoleBinding:        ServiceAccount "metrics-agent" → Role "pod-reader"
```
By default every pod mounts a ServiceAccount token. Most app pods don't need it; set `automountServiceAccountToken: false`.

**Cloud access from pods:** on EKS use **Pod Identity** (or older IRSA) to map a ServiceAccount to an IAM role, so a pod gets AWS credentials without long-lived keys. Never bake keys into images.

**Pod hardening checklist:** run as non-root, read-only root filesystem, drop all Linux capabilities, no privilege escalation, resource limits, NetworkPolicy, no host mounts, pinned image digests, scan images.

**Who can run kubectl:** the cluster's auth integrates with your cloud identity (EKS access entries / aws-auth, GKE IAM).

---

## 11. Namespaces and organizing a cluster

A **namespace** is a folder for objects with its own names, RBAC, quotas, and policies. `default`, `kube-system` (cluster plumbing), then yours (`shop`, `monitoring`). Not a security boundary on its own (pods can still talk across namespaces unless a NetworkPolicy blocks it).

- `ResourceQuota` caps total CPU/memory/objects in a namespace.
- `LimitRange` sets default requests/limits.
- Common split: one namespace per team or per environment.

---

## 12. Packaging and extending

**Helm** = templated YAML + a versioned install record. A *chart* is templates + default `values.yaml`; a *release* is one install with your overrides. `helm install`, `helm upgrade`, `helm rollback`, `helm template` (preview). Alternative: **Kustomize** (patch plain YAML per environment, built into kubectl).

**CRD + operator.** A *CustomResourceDefinition* adds a new object type to the API (`Certificate`, `Prometheus`, `NodePool`). An *operator* is a controller that watches that type and acts. This is how most infrastructure is installed: cert-manager, Prometheus operator, Karpenter, KEDA, Postgres operators. Same reconcile loop as section 1, for your own nouns.

---

## 13. The request, end to end (public backend)

```
 POST https://api.shop.com/orders
   1. DNS api.shop.com  → cloud load balancer IP
   2. cloud LB (L4)     → a node, to the ingress controller's port
   3. node kube-proxy   → one ingress-nginx pod
   4. nginx (L7)        → matches Host+path to an Ingress rule → Service "shop"
                          picks one Ready pod IP from the Service's endpoints
   5. shop pod          → handles /orders, queries "postgres" (Service → DNS → pod IP)
   6. postgres-0        → reads/writes its PVC-backed disk
   7. response retraces the same hops
```
Where the control plane appears: nowhere on this path. It placed the pods, filled the endpoint lists, and will react if a pod dies.

---

## 14. Observability and debugging

**The metrics path:** app exposes `/metrics` → Prometheus scrapes it (a ServiceMonitor tells the Prometheus operator what to scrape) → Grafana charts it. `kube-state-metrics` turns Kubernetes objects into metrics too.

**The commands**

```
kubectl get pods -A -o wide                  # everything, with node and IP
kubectl describe pod X                       # events: why Pending, why it restarted
kubectl logs X [-c container] [--previous]   # app output
kubectl exec -it X -- sh                     # shell inside
kubectl get events --sort-by=.lastTimestamp
kubectl get endpoints SERVICE                # are there backends?
kubectl port-forward svc/shop 8080:80        # laptop tunnel through the API server
kubectl top pods / nodes                     # live CPU/memory (metrics-server)
kubectl rollout status|history|undo deployment/shop
kubectl auth can-i delete pods --as system:serviceaccount:ns:sa   # RBAC check
```

**Debugging ladder when "it's not working":**
```
 1. kubectl get pods            → Pending? CrashLoop? not Ready?
 2. kubectl describe pod        → events at the bottom say why
 3. kubectl logs --previous     → the app's own error
 4. kubectl get endpoints svc   → empty? label/selector or readiness problem
 5. nslookup svc from a pod     → DNS?
 6. nc -zv podIP port / curl    → network path, NetworkPolicy, security group?
```

---

## 15. Managed Kubernetes (EKS) in one table

| | You manage | AWS manages |
|---|---|---|
| Control plane (API server, etcd, scheduler) | no | yes, and you can't see the machines |
| Worker nodes | yes (or Karpenter / managed node groups / Fargate) | AMI patches optionally |
| CNI (VPC CNI), CoreDNS, kube-proxy | as EKS addons | versions on request |
| Pod IPs | from your VPC subnets | |
| Load balancers | via the AWS Load Balancer Controller | the LB itself |
| Auth | IAM → access entries / RBAC | |
| Upgrades | you trigger and sequence them | the control plane rollout |

---

## 16. Cheat sheet: what to say in an interview

- **What is Kubernetes?** A declarative orchestrator: a state store plus reconcile loops that place and keep containers running.
- **Control plane vs nodes?** Control plane (API server, etcd, scheduler, controller-manager) decides and records; nodes (kubelet, runtime, kube-proxy) execute.
- **Who talks to whom?** Everything talks to the API server. Components watch it. Nothing bypasses it.
- **Pod vs Deployment?** A pod is one disposable unit; a Deployment keeps N of them alive and rolls out versions.
- **How do pods find each other?** Services: stable name + virtual IP, implemented by CoreDNS and kube-proxy.
- **How does traffic get in from the internet?** Cloud LB → ingress controller (nginx/ALB) → Service → pod.
- **requests vs limits?** Requests are what the scheduler reserves; limits are the kernel's ceiling.
- **liveness vs readiness?** Liveness restarts; readiness removes from the Service.
- **HPA vs cluster autoscaler?** HPA scales pods; the autoscaler/Karpenter adds nodes when pods can't be scheduled.
- **How do pods get AWS access?** ServiceAccount mapped to an IAM role (Pod Identity/IRSA). Not keys.
- **What is an operator?** A controller for a custom resource type: the same reconcile loop for your own objects.
- **StatefulSet vs Deployment?** Stable names, ordered start, per-pod persistent volumes.

---

## Where to go next

`PROJECT-WALKTHROUGH.md` applies all of this to pr-runtime, with the specific diagrams and the bugs we hit. `ONBOARDING.md` is the longer reference for the same material. `DIAGRAMS.md` has the big pictures of our cluster and the full system.
