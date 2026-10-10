# pr-runtime walkthrough: how this specific cluster works

Project-specific. For general Kubernetes, read CRASH-COURSE.md first. One idea per section, one small diagram each, every example is an object you can `kubectl get` in our cluster. Skip anything you already know. `ONBOARDING.md` is the reference version; `DIAGRAMS.md` has the big pictures.

---

## 0. The only mental model you need

Kubernetes is a **database of desired state** plus **loops that make reality match it**. You never tell it "start a container." You write "I want 4 of these" and something reconciles.

```mermaid
flowchart LR
  YOU["you / Helm / our controller"] -- "write desired state (YAML)" --> API["API server"]
  API <--> ETCD[("etcd")]
  API -- "watch" --> C["a controller loop<br/>observe, diff, act"]
  C -- "create / delete / patch" --> API
  API -- "watch" --> K["kubelet on a node<br/>start / stop containers"]
  K -- "report status" --> API
```

Everything below is a specific instance of this loop. Slurm analogy: the controller daemon plus the power-save scripts, generalized to every kind of object.

---

## 1. Control plane vs nodes: who does what

```mermaid
flowchart TB
  subgraph CP["Control plane (EKS runs it; not on our nodes)"]
    API["kube-apiserver<br/>REST + auth + validation"]
    ETCD[("etcd<br/>state")]
    SCHED["kube-scheduler<br/>picks a node for each new pod"]
    KCM["controller-manager<br/>ReplicaSet, Deployment, StatefulSet, Job, PVC loops"]
  end
  subgraph N["each worker node (3x m7g.large + Karpenter burst)"]
    KUBELET["kubelet<br/>runs what the API says, runs probes, reports"]
    CRI["containerd<br/>pulls images, runs containers"]
    KPROXY["kube-proxy<br/>Service routing rules"]
    CNI["VPC CNI<br/>pod IPs + NetworkPolicy"]
  end
  SCHED --> API
  KCM --> API
  API <--> KUBELET
  KUBELET --> CRI
```

- **kubelet** decides nothing. It is told "run pod X" and does it.
- **scheduler** decides *where*. **controllers** decide *how many*. **Karpenter** (section 9) decides *how many nodes*.
- Our controller pod is just another API client. It lists runner pods and deletes them. Kubernetes has no idea what a "task" is.

### Life of a pod creation

```mermaid
sequenceDiagram
  participant H as helm / kubectl
  participant A as API server
  participant R as ReplicaSet controller
  participant S as scheduler
  participant K as kubelet (node 2)
  participant C as containerd
  H->>A: apply Deployment runner (replicas=4)
  A->>R: watch: new Deployment
  R->>A: create 4 Pod objects (no node yet)
  A->>S: watch: 4 Pending pods
  S->>A: bind pod to node 2 (fits requests, matches nodeSelector)
  A->>K: watch: pod assigned to me
  K->>C: pull image from ECR, start container
  K->>A: status Running, readiness probe result
```

That is the whole system. Every other object is a variation on "someone watches, someone acts."

---

## 2. Deployment, ReplicaSet, Pods: our warm pool

```mermaid
flowchart LR
  D["Deployment runner<br/>replicas: 4, image tag"] --> RS["ReplicaSet<br/>keeps exactly 4 pods matching app=runner"]
  RS --> P1["runner-abc (Ready, idle)"]
  RS --> P2["runner-def (Ready, idle)"]
  RS --> P3["runner-ghi (NotReady, busy)"]
  RS --> P4["runner-jkl (Ready, idle)"]
```

```mermaid
sequenceDiagram
  participant Ctl as our controller
  participant A as API server
  participant RS as ReplicaSet controller
  participant Pod as runner-ghi
  Ctl->>Pod: POST /task (pod IP:8080)
  Pod->>Pod: fetch, pytest, ruff
  Pod->>Ctl: POST /result/{id}
  Pod->>Pod: readiness 503 (never reassigned)
  Ctl->>A: delete pod runner-ghi
  A->>RS: watch: only 3 pods match
  RS->>A: create runner-mno
  Note over RS,Pod: new pod boots warm: image cached, seed clone copied to /work
```

Why delete instead of letting the process exit: a Deployment pod has `restartPolicy: Always`, so an exited container restarts **in the same pod** with the same dirty `/work`. Pod *deletion* is the ephemeral unit; the ReplicaSet gives us the refill for free.

Other pod-makers you will see: **StatefulSet** (Postgres: stable name `postgres-0`, its own disk), **DaemonSet** (one per node: CNI, kube-proxy, pod-identity agent), **Job** (run once: the KEDA baseline).

---

## 3. Readiness probe as the idle flag

```mermaid
stateDiagram-v2
  [*] --> Booting: pod created
  Booting --> Idle: seed copied, healthz 200, Ready
  Idle --> Busy: controller POST /task, healthz 503, NotReady
  Busy --> Done: result posted, stays 503
  Done --> [*]: controller deletes pod
```

The controller's definition of "idle runner" = **Ready and not in my assigned set**. No labels, no extra state. Kubelet runs the probe every 2s; readiness also controls whether a pod receives Service traffic (irrelevant for runners, they have no Service; crucial for the controller, which does).

---

## 4. Requests, limits, and where pods land

```mermaid
flowchart LR
  subgraph node["m7g.large: 2 vCPU, 8 GiB; allocatable about 1.9 CPU / 7 GiB"]
    A["controller<br/>req 250m"]
    B["runner<br/>req 500m"]
    C["runner<br/>req 500m"]
    D["postgres<br/>req 100m"]
    E["daemonsets<br/>about 300m"]
  end
  F["runner req 500m"] -. "does not fit: Pending" .-> node
```

- **requests** = what the scheduler reserves. Sum must fit the node's allocatable.
- **limits** = cgroup ceiling. CPU over limit is throttled; memory over limit is OOM-killed.
- Pending pods are the signal Karpenter reacts to (section 9).
- `nodeSelector: pr-runtime/pool: general` pins controller + Postgres to the fixed nodes. Runners have none, so they float onto burst nodes.

---

## 5. Networking in five packets

### 5a. Pod IPs: every pod is a real VPC address

```mermaid
flowchart LR
  subgraph n1["node 10.0.11.129"]
    p1["controller 10.0.19.130"]
  end
  subgraph n2["node 10.0.27.25"]
    p2["runner 10.0.12.110"]
  end
  p1 -- "POST http://10.0.12.110:8080/task<br/>plain VPC routing, no NAT, no Service" --> p2
```

The **VPC CNI** hands pods secondary IPs from the node's ENI. So our controller can talk to a specific runner by IP, which is exactly what it wants: *this* pod, not "any runner." Pod IPs change on replacement; the controller re-lists every tick.

### 5b. Services: a stable name over moving pods

```mermaid
flowchart LR
  R["runner pod"] -- "POST http://controller:8000/result/{id}" --> DNS["CoreDNS<br/>controller resolves to 172.20.x.y"]
  DNS --> VIP["Service controller<br/>ClusterIP 172.20.x.y:8000"]
  VIP -- "kube-proxy iptables to a Ready backend" --> C["controller pod 10.0.19.130:8000"]
```

- Service = virtual IP + DNS name + label selector. kube-proxy on every node rewrites the virtual IP to a Ready pod IP.
- `postgres` is **headless** (`clusterIP: None`): DNS returns the pod IP directly. Normal for StatefulSets.
- Service types: ClusterIP (ours, internal only), NodePort, LoadBalancer (would create an AWS LB; we have none, so nothing in the cluster is reachable from outside).

### 5c. Out to the internet: private subnets and NAT

```mermaid
flowchart LR
  R["runner pod<br/>private subnet"] -- "git fetch github.com:443" --> NAT["NAT gateway<br/>public subnet"] --> GH["github.com"]
  C["controller pod"] -- "api.openai.com, api.github.com, SQS, Secrets Manager" --> NAT
  X["internet"] -. "no route in" .-> R
```

Nodes have no public IPs. Inbound is impossible by construction; outbound goes through one NAT gateway. The webhook never enters the cluster: the controller **pulls** from SQS.

### 5d. NetworkPolicy: the runner's firewall

```mermaid
flowchart LR
  R["runner pod"]
  CTRL["controller"]
  subgraph allowed["allowed egress"]
    A1["controller:8000"]
    A2["kube-dns:53"]
    A3["any IP :443<br/>except 10.0.0.0/8 and 169.254.169.254"]
  end
  subgraph blocked["blocked (verified)"]
    B1["postgres:5432"]
    B2["example.com:80"]
    B3["169.254.169.254 (EC2 metadata = node creds)"]
    B4["other pods"]
  end
  R --> allowed
  R -. "x" .-> blocked
  CTRL -- "ingress :8080 only from app=controller" --> R
```

Needs CNI enforcement (we turned on `enableNetworkPolicy` in the VPC CNI; an eBPF agent on each node applies it). Honest gap: port 443 to any public host, because GitHub's IPs are not pinnable.

### 5e. Getting in from your laptop

```mermaid
flowchart LR
  B["browser localhost:18000"] --> L["laptop<br/>kubectl port-forward svc/controller 18000:8000"]
  L -- "HTTPS, IAM token" --> API["EKS API endpoint"]
  API -- "tunnel via kubelet" --> C["controller pod :8000"]
```

No public endpoint for the console or Grafana. `port-forward` is an authenticated tunnel through the API server. It dies when the pod is replaced or the laptop sleeps (most of our "it hung" moments).

Inside the VPC the API endpoint resolves to **private** IPs behind the **cluster security group**, which is why the driver box needed a 443 rule added (`infra/driver.tf`).

---

## 6. Storage: PVC to PV to EBS

```mermaid
flowchart LR
  SS["StatefulSet postgres"] --> PVC["PVC data-postgres-0<br/>8Gi, class gp3"]
  PVC --> CSI["EBS CSI driver<br/>(addon, Pod Identity role)"]
  CSI --> EBS["EBS volume<br/>in the pod's AZ"]
  EBS --> PV["PersistentVolume<br/>bound to the PVC"]
  PV -. "mounted at /var/lib/postgresql/data" .-> POD["postgres-0"]
```

- **emptyDir** = scratch tied to the pod's life (runner `/work`, controller `/tmp` with the git mirror).
- **PVC** = a claim ("8Gi gp3"); the **StorageClass** names the driver; the **CSI driver** creates and attaches the real disk.
- EBS is AZ-bound: `postgres-0` can only be rescheduled in its AZ. RDS is the prod answer.

---

## 7. Config and secrets: three sources, one rule

```mermaid
flowchart TB
  CM["ConfigMap<br/>eval-results, guidelines, grafana dashboard"] -- "mounted as files" --> C["controller / grafana"]
  SEC["k8s Secret pr-runtime-env<br/>(from .env via sync_secret.sh)"] -- "env POSTGRES_PASSWORD" --> PG["postgres-0"]
  SM["AWS Secrets Manager<br/>pr-runtime/app"] -- "GetSecretValue at boot<br/>via Pod Identity" --> C
  C -. "never" .-> R["runner pod"]
```

Rule: the **runner gets nothing**. The controller reads one AWS secret at boot (no k8s Secret mounted into it). Postgres still uses a k8s Secret for its own password.

---

## 8. Identity: two systems, keep them apart

```mermaid
flowchart LR
  subgraph K8S["Kubernetes RBAC (inside)"]
    SA["ServiceAccount controller"] --> RB["RoleBinding"] --> ROLE["Role: get/list/watch/delete pods"]
  end
  subgraph AWS["AWS IAM (outside)"]
    PIA["Pod Identity association<br/>ns pr-runtime / SA controller"] --> IAMROLE["IAM role pr-runtime-controller<br/>sqs:Receive, secretsmanager:GetSecretValue"]
  end
  CTRL["controller pod"] --> SA
  CTRL --> PIA
  RUN["runner pod"] -. "no SA token, no AWS role" .-> X["nothing"]
  USER["you / driver box (IAM principals)"] -- "EKS access entry: cluster-admin" --> K8S
```

- RBAC answers "may this pod call the Kubernetes API?" Our controller: pods only, one namespace.
- Pod Identity answers "may this pod call AWS?" (IRSA is the older OIDC-token way; same idea.)
- Access entries answer "may this IAM user or role run kubectl?"

---

## 9. Autoscaling: three different things

| Tool | Scales | Signal | Us |
|---|---|---|---|
| HPA | pods | CPU/memory | not used |
| **KEDA** | pods / Jobs | external metric (SQS depth, ...) | cold-start baseline only, disabled |
| **Karpenter** | **nodes** | Pending pods | burst capacity |
| our controller | tasks onto pods | its own queue | the actual scheduler of work |

```mermaid
sequenceDiagram
  participant U as kubectl scale runner --replicas=16
  participant A as API server
  participant S as scheduler
  participant K as Karpenter
  participant EC2 as EC2
  U->>A: replicas 4 to 16
  A->>S: 12 new pods
  S->>A: 5 fit on the floor, 7 Pending (no room)
  A->>K: watch: 7 Pending pods, 500m each
  K->>EC2: CreateFleet: cheapest arm64 in m7g/c7g/r7g/m8g/c8g that fits, spot preferred
  EC2-->>K: c7g.2xlarge spot, Ready at t+41s
  K->>A: node registered, label pool=burst
  S->>A: bind the 7 pods to it (t+49s)
  Note over K: pool back to 4, node empty 60s, drain + terminate
```

Spot reclaim: AWS posts a 2-minute warning to Karpenter's SQS interruption queue; Karpenter drains early; a busy runner's pod vanishes; our controller notices on the next tick and requeues the task (`prr_tasks_lost_total{reason=pod_lost}`). Measured: attempt 2 posted in 50s, nothing failed.

Slurm mapping: NodePool = partition/nodeset rules; Karpenter = ResumeProgram/SuspendProgram with the machine type chosen per request; consolidateAfter = SuspendTime; `kubectl drain` = node going DRAIN.

---

## 10. Helm, CRDs, operators

```mermaid
flowchart LR
  V["values.yaml<br/>poolSize, cap, models, prices"] --> T["templates/*.yaml"] --> H["helm upgrade --install"] --> OBJ["Deployment, Service, StatefulSet,<br/>NetworkPolicy, ServiceMonitor"]
  CRD["CRD = new object type<br/>NodePool, ScaledJob, ServiceMonitor"] --> OP["operator = controller for it<br/>Karpenter, KEDA, Prometheus operator"]
  OP -- "same loop as section 0" --> OBJ
```

Helm is templating plus a release record; nothing magic. A CRD plus its operator is how Kubernetes grows new nouns: we installed three (Prometheus operator, KEDA, Karpenter) and wrote none.

---

## 11. Observability path

```mermaid
flowchart LR
  C["controller /metrics<br/>prometheus_client"] --> SM["ServiceMonitor<br/>scrape every 5s"] --> P["Prometheus<br/>time series"] --> G["Grafana<br/>PromQL panels"]
  KSM["kube-state-metrics<br/>pod/node objects as metrics"] --> P
```

PromQL you will see: `max(prr_tasks_pending)` (gauge, collapsed across restarts), `sum(rate(prr_tasks_total[1m]))` (counter to rate), `histogram_quantile(0.95, sum(rate(prr_time_to_comment_seconds_bucket[2m])) by (le))` (p95).

---

## 12. One PR, end to end

```mermaid
sequenceDiagram
  participant GH as GitHub
  participant L as API GW + Lambda
  participant Q as SQS
  participant C as controller
  participant R as runner pod
  participant O as OpenAI
  participant PG as Postgres
  GH->>L: pull_request webhook (HMAC signed)
  L->>Q: SendMessage (signature verified)
  C->>Q: ReceiveMessage (long-poll via NAT)
  C->>PG: insert task (priority = changed lines)
  C->>R: POST /task to warmest idle pod IP
  R->>GH: anonymous git fetch (NAT, 443)
  R->>R: pytest, ruff, read touched files
  R->>C: POST /result/{id}, readiness 503
  C->>C: delete pod (ReplicaSet refills)
  C->>O: Responses API, JSON schema, effort high
  C->>PG: findings, verdict, cost
  C->>GH: POST pulls/N/reviews (serialized)
  C-->>C: SSE event, console updates
```

Timing, quiet: assigned under 1s, runner about 4s, LLM about 15s, post about 2s, total about 24s. Under a 56-PR burst with cap 4: p50 68s, p95 200s; the queue wait and the LLM stage dominate, never the runners.

---

## 13. What broke, and the Kubernetes lesson in each

| What happened | Lesson |
|---|---|
| Exit-after-one restarted in place | Deployment pods restart containers; delete the pod to get a fresh one |
| Controller got drained with a spot node | Elastic nodes need `nodeSelector`/affinity for anything stateful or control-plane |
| Busy pod vanished, task waited 300s | Watch the live pod set; do not rely on deadlines for node loss |
| 18 reviews in flight when the controller restarted | In-memory state dies; reconcile from the database on boot |
| Driver box could not reach the API | In-VPC traffic hits the private endpoint behind the cluster SG |
| `port-forward` "hangs" | It is bound to one pod; dies on rollout |
| One cap for runners and LLM calls | Stages with different resource profiles need separate concurrency |
| Spot launch failed | Account-level prerequisites (service-linked role, quotas) come before any scheduler logic |

---

## 14. Twenty questions, one line each

1. What does kubelet do? Runs the pods the API server assigns to its node, runs probes, reports status. Decides nothing.
2. What places a pod on a node? kube-scheduler, by requests plus nodeSelector/affinity/taints.
3. What keeps 4 runners alive? The ReplicaSet behind the Deployment.
4. How does the controller know a runner is idle? Pod Ready (probe 200) and not in its assigned set.
5. Why delete the pod instead of exiting? `restartPolicy: Always` restarts in place with the dirty workdir.
6. How does the controller reach a runner? Pod IP from the API, HTTP to :8080. Pods have real VPC IPs.
7. How does the runner reach the controller? Service DNS `controller`, ClusterIP, kube-proxy, the pod.
8. Why no LoadBalancer Service? Nothing in the cluster is public; the webhook goes to API Gateway, Lambda, SQS, and the controller pulls.
9. What stops a runner from reading Postgres or EC2 metadata? NetworkPolicy, enforced by the VPC CNI eBPF agent. Verified.
10. What is the hole in it? Any host on 443.
11. How does the controller get AWS credentials? Pod Identity association to an IAM role, via the node agent.
12. How does it get Kubernetes permissions? ServiceAccount plus Role (pods: get/list/watch/delete) in one namespace.
13. How does kubectl from the driver box authenticate? IAM role, EKS access entry, cluster-admin.
14. What is a PVC? A claim for storage; the EBS CSI driver turns it into an AZ-bound EBS volume.
15. What does Karpenter react to? Pending pods. It picks the cheapest instance that fits them and consolidates idle nodes after 60s.
16. What happens on a spot reclaim mid-task? Interruption queue, drain, pod vanishes, controller requeues immediately.
17. KEDA vs Karpenter? KEDA scales pods/Jobs from external metrics; Karpenter scales nodes. We kept KEDA only as the cold-start baseline.
18. What is a CRD? A new object type; its operator is the controller loop that acts on it (NodePool, ScaledJob, ServiceMonitor).
19. What does Helm do? Templates YAML and records the release. `helm template` shows what it applies.
20. If the controller dies mid-review? The SQS message was already deleted; the row is in Postgres; `reconcile()` on boot resumes reviewing tasks and requeues running ones. Measured: 17 reviews resumed.
