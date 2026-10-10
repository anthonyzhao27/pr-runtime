# pr-runtime — Spec (as built)

**One-line:** A pod-per-PR code review runtime on EKS. Every pull request gets its own ephemeral, secret-less pod that clones the branch and runs the test suite; a trusted controller admits work from a queue, keeps a warm pool so nothing waits on a cold start, asks an LLM to review the diff with the test evidence attached, and posts inline review comments back to GitHub. An eval harness with real and synthetic injected bugs measures whether the reviewer catches what CI misses, and what each kind of context costs.

**Why this exists (the talk's framing):** Uncountable's engineering post describes Circus: ingress → control plane that ranks and admits → warm pod pool → ephemeral pod per task → egress, with Rover reviewing every PR. The question this project answers for myself: *what does it actually cost to run untrusted PR code per-task on Kubernetes, and what does a warm pool / admission layer buy you under burst?* The reviewer is the workload. The measurements are the deliverable.

**Dates:** Oct 7 build start → core done Oct 7 night → stretch done Oct 8 → **video by Oct 13** → Oct 14 travel → Oct 15 onsite (30 min: ~12 talk, ~18 Q&A).

This file was rewritten on Oct 9 to match what is deployed. The original plan's deviations are called out inline as **(changed)**; the reasons live in `docs/DECISIONS.md`.

---

## 1. Decisions (as built)

Each row: what we chose, what we rejected, and the one-sentence Q&A answer.

| # | Decision | Chosen | Rejected | Q&A line |
|---|---|---|---|---|
| 1 | Target repo | Fork of `pallets/flask` (`anthonyzhao27/flask`) | Own toy repo; SQLAlchemy | "Real code I didn't write, 494 tests in ~1 second, and you read Flask daily so you can judge whether a catch is good." |
| 2 | Ingress **(changed)** | GitHub webhook → API Gateway → **Lambda (HMAC verify)** → SQS | API GW's direct SQS integration (cannot forward the signature header); ALB into the cluster; polling | "Nothing in the cluster is public and nothing unauthenticated reaches the queue. The queue is the burst buffer." |
| 3 | Control plane | Custom Python controller (`kubernetes` client) | Pure KEDA ScaledJob (kept as the cold-start baseline in `deploy/baseline`) | "KEDA gives pod-per-task in 20 lines but no warm pool, no ranking, and the pod has to hold AWS creds to pull its own message." |
| 4 | Warm pool mechanism **(changed)** | Runner `Deployment` of N idle pods; controller assigns via HTTP to pod IP; after one task the runner flips readiness to 503 and the **controller deletes the pod**; ReplicaSet refills | Exit-after-one (a Deployment restarts the container in place with a dirty workdir); reusing pods; Jobs | "Pod deletion is the ephemeral boundary; the ReplicaSet is the refill logic for free." |
| 5 | Admission **(extended)** | Cap M on concurrent busy runners, **M = Ready runner pods by default** (`ADMISSION_CAP=0`; positive = fixed override); rank pending by diff size (small first); separate `REVIEW_WORKERS` (32) for the LLM stage; busy pod disappears → immediate requeue (attempts ≤ 3). Pool autoscaled by a KEDA ScaledObject on `prr_tasks_pending` (4..16) + Karpenter; measured Oct 10: 16 runners in 78s, runner stage 86s vs ~105s fixed, but time-to-comment p50 165s vs 70s because serialized GitHub posting became the queue; third run (App posting, one gap per task) p50 81s / p95 143s | FIFO; one concurrency knob for both stages; a static cap next to an elastic pool | "One cap is wrong for a pipeline whose stages have different resource profiles: runners are CPU-bound for 4s, the LLM stage is I/O-bound for 15-60s. And when the cap follows the pool, the next bottleneck is the egress." |
| 6 | Trust boundary | **Split.** Runner pod has zero secrets, clones the public fork anonymously, runs tests, POSTs results back. Controller holds all secrets (from Secrets Manager), calls the LLM, posts the review, and serves a read-only git mirror for the agentic tools. | Runner does everything | "A malicious `conftest.py` can exfiltrate anything the pod can see. So the pod sees nothing." |
| 7 | Pod hardening | `runAsNonRoot`, `readOnlyRootFilesystem` + `/work` emptyDir (seed clone copied in at boot), `automountServiceAccountToken: false`, drop all caps, CPU/mem limits, controller-enforced deadline, NetworkPolicy: ingress only from controller; egress only to controller, DNS, and **any host on 443 except RFC1918 and IMDS** (GitHub's CIDRs are not fixed) | Default pod spec | "Not a sandbox. gVisor or Firecracker is the real answer; this is the cheapest layer that stops the obvious attack. The 443 allow-list is the hole." |
| 8 | LLM step **(extended)** | Structured call (JSON schema) with `reasoning.effort=high`. Four configs: `diff_only`; `full` (+ touched files + pytest/ruff + `read_file` ≤5); `guided` (full + mined guidelines); `agentic` (full + `read_file`/`grep`/`list_dir` against the mirror, ≤15 calls) | Single config | "Single structured call keeps the eval stable. Everything else is an ablation arm, and none of them beat the diff on this corpus." |
| 9 | Output **(extended)** | Inline review comments via GitHub Reviews API + `APPROVE` / `REQUEST_CHANGES` verdict as `pr-runtime[bot]`, plus a **Check run** (`failure` on blockers, never approves). PAT fallback downgrades to `COMMENT` when the reviewer authored the PR. **When the runner cannot run** (private repo it cannot clone, bad ref, crash) the controller fetches the diff and touched files through the API and still posts a review, labelled "Tests not run: <reason>. Review is from the diff only." | One summary comment; failing the task when the runner fails | "file:line precision is the whole point; a blob comment hides whether it actually found the bug. And a PR the runner can't clone still gets the diff review, it just says so." |
| 10 | Test selection | Full `pytest` every time | Affected-tests only | "Suite is 1-3 seconds. Selection logic is a bug farm I didn't need." |
| 11 | Models | Reviewer: `gpt-6-astra`, effort high. Judge: `gpt-6-luna`. IDs pinned in Helm values, logged per task. | Same model for both | "Judge is a different, 100× cheaper model so the reviewer isn't grading itself." |
| 12 | Eval ground truth **(changed)** | 46 bugs: 8 historical reverted Flask fixes (red + green variants; green variants hide the test removal in a base branch), 22 synthetic single-operator mutants the suite does not catch, 10 red mutants, 11 "noisy" mutants hidden inside a real multi-file upstream diff; 20 real merged upstream changes replayed as clean PRs. Equivalent mutants excluded. Strict (±15 lines) and judge-semantic scoring. | 30 historical only (history yields ~8 usable); hand-labeled bugs | "Historical = bugs maintainers actually shipped. Synthetic = defects nothing tests. The harness was wrong more often than the reviewer." |
| 13 | IaC **(extended)** | Terraform: VPC, EKS (`terraform-aws-modules/eks` v21), node group, SQS+DLQ, API GW + Lambda, ECR ×3, **Pod Identity** roles, EBS CSI addon, Secrets Manager, Karpenter IAM/queue, driver instance. Helm releases applied by CLI/Makefile, not Terraform. | eksctl; IRSA | "One repo describes everything, one `destroy` tears it down. Pod Identity instead of IRSA: one association per service account, no OIDC juggling." |
| 14 | Nodes **(changed)** | Fixed floor: 3× `m7g.large` on-demand Graviton. Burst: **Karpenter** NodePool (m7g/c7g/r7g/m8g/c8g, large–2xlarge, spot preferred, on-demand fallback, consolidate after 60s). Controller and Postgres pinned to the floor. | Fixed group only; x86 | "The floor is for recording reliability; Karpenter is what you'd run at scale. 4→16 runners in 49s; a drained spot node mid-task costs one requeue." |
| 15 | Observability **(changed)** | kube-prometheus-stack via Helm (5s scrape, anonymous viewer); controller `/metrics`; Grafana dashboard JSON loaded by ConfigMap. No Rich table (never needed). | CloudWatch custom metrics | "Grafana at 5s refresh is what the burst video shows; CloudWatch refreshes once a minute." |
| 16 | Demo | Pre-recorded video, cluster may be down on Oct 15 | Live demo | "Travelling the night before. Zero live risk." |
| 17 | Secrets **(changed)** | **Secrets Manager** (`pr-runtime/app`) read by the controller at boot via Pod Identity. k8s Secret from `.env` remains only for Postgres's own password. Driver box materializes `.env` from the same secret. | k8s Secret into the controller (the 6-day answer, replaced on day 2) | "Nothing is mounted into the controller; it assumes a role and reads one secret. Caveat: values are seeded from `.env` by Terraform, so they sit in local state." |
| 18 | Task console (full-stack) | React 18 + Vite + TypeScript SPA built in the controller image and served by FastAPI; JSON API + SSE; Postgres for tasks/findings/feedback; cost column; Eval page reads a published summary | FastAPI + Jinja + HTMX; custom "observability dashboard" | "Grafana owns runtime health. The console is the product surface: what did the reviewer find, was it right, re-run it. Thumbs-down is how engineers teach the reviewer." |
| 19 | Database **(changed)** | Postgres 16 (`postgres:16-alpine`) as a StatefulSet in our own Helm chart, 8Gi gp3 PVC via EBS CSI | `bitnami/postgresql` (image-tag churn); RDS; SQLite | "Their DB, in-cluster, no extra AWS bill. RDS is the prod answer." |
| 23 | GitHub App **(new, S4)** | App auth: JWT → per-installation 1h tokens; triggers: every PR or `@pr-runtime` mention; `.pr-runtime.yml` `mode:` per repo | Repo webhook + PAT (kept as fallback) | "Install once, any repo; least-privilege short-lived tokens; the bot blocks via a required check and never approves." |
| 24 | Multi-repo **(new, S8)** | Runner clones any repo on demand, detects the toolchain (uv/pip/npm/go) or reads `.pr-runtime.yml`; seed repo stays warm | Flask-only baked image | "Click, never seen before: clone 0.4s, install 3s, tests 15s. The warm clone buys ~15s, almost all of it the test suite." |
| 20 | Framing | Infra-question framing with eval as second act. Name Circus on slide 2. | "I built an AI code reviewer" | "You wrote about X. I wanted to measure Y for myself." |
| 21 | Driver box **(new)** | `t4g.large` arm64 EC2 in a private subnet, SSM only, cluster-admin via EKS access entry; all image builds and eval runs happen there (`scripts/driver.sh`) | Building and running evals on the laptop (it kernel-panicked from memory pressure on Oct 8) | "The thing that must stay up should not be the thing you close the lid on." |
| 22 | Cost model **(new)** | Per task: compute = runner seconds × node $/hr × CPU share; tokens at list price; standing pool $/hr | None | "Tokens are 1000× the compute. Four idle runners cost $0.16/hr ≈ 2,000 reviews' worth of tokens per month." |

---

## 2. Architecture (as deployed)

```
GitHub App "pr-runtime" (installed per repo/org; events for every installed repo)
   │  pull_request (opened / synchronize / reopened / ready_for_review)  ·  issue_comment mentioning @pr-runtime
   ▼
API Gateway (HTTP API) ──► Lambda: verify HMAC, drop pings/drafts/non-mentions ──► SQS pr-runtime-tasks (+ DLQ)
                                                                   │ long-poll
                                                                   ▼
   Secrets Manager ──(Pod Identity)──►  ┌─────────────────────────────┐
   pr-runtime/app                       │  controller (FastAPI, 1 pod) │  pinned to fixed nodes
                                        │  - SQS consume, dedupe      │
                                        │  - rank (diff size), admit  │  cap M = Ready runners (KEDA 4..16)
                                        │  - assign → delete pod      │  REVIEW_WORKERS=32
                                        │  - lost-pod / deadline requeue, reconcile on restart
                                        │  - LLM review (4 configs)   │  git mirror in /tmp for agentic tools
                                        │  - App JWT → install token  │  review + Check run as pr-runtime[bot]
                                        │  - serialized GitHub posts  │
                                        │  - /metrics  /api  SSE      │◄──► Postgres 16 (StatefulSet, gp3 PVC)
                                        │  - serves console + eval    │
                                        └──────────┬──────────────────┘
                                                   │ POST /task {task_id, head, base, pr}
                                                   ▼
                        ┌──────────────────────────────────────────┐
                        │ runner Deployment, replicas 4..16        │  no secrets, no SA token, read-only root
                        │  (KEDA ScaledObject → HPA on backlog)    │  egress: controller, DNS, *:443 (not RFC1918/IMDS)
                        │  boot: stage seed repo → /work           │
                        │  task: seed repo warm, else clone any    │
                        │        repo; detect toolchain or read   │
                        │        .pr-runtime.yml; tests + lint;   │
                        │        POST /result, readiness→503      │
                        │  controller deletes pod; ReplicaSet refills
                        └──────────────────────────────────────────┘
                 fixed floor: 3× m7g.large ──── Karpenter burst: spot c7g/m7g…, consolidates at 60s idle

   Prometheus (5s) ◄── scrape ──► Grafana (anonymous viewer, dashboard "pr-runtime")
   driver box (t4g.large, SSM) ── builds images → ECR, runs evals, helm rollout
```

**Flow for one PR:**
1. The GitHub App delivers the event → Lambda verifies the HMAC, keeps PR events and `@pr-runtime` comments, forwards to SQS with `event`/`action`/`delivery` attributes.
2. Controller consumes SQS, checks the repo allowlist and the repo's `.pr-runtime.yml` `mode:`, dedupes by `(pr, head_sha, config)`, supersedes older in-flight tasks for the same PR, creates a `tasks` row (priority = changed lines, `installation_id`, `trigger`), opens a Check run, enqueues. A mention gets a 👀 reaction.
3. Scheduler tick (1s): idle = runner pods that are Ready and not assigned; cap M = number of Ready runners (or a fixed override); while `busy < M`, pop smallest, POST `{repo, clone_url, shas}` to the warmest idle pod; `cold` flag if the pod is < 10s old. Backlog raises `prr_tasks_pending` → KEDA scales the Deployment 4..16 → Karpenter adds a node if needed.
4. Runner: reuse the warm seed repo or partial-clone the task's repo, `git fetch`, checkout, merge-base diff, detect the toolchain (or read `.pr-runtime.yml`), install if needed, run tests and lint, read touched files, POST `/result/<task_id>`, flip readiness to 503.
5. Controller deletes the pod, persists the result, submits the review to the LLM pool (32 workers). Config decides the prompt: diff only / + files + test output / + guidelines / + tools. If the runner reported an error instead (private repo, bad ref, crash), the controller fetches the diff and touched files from the GitHub API with the installation token and reviews as `diff_only`, flagged `meta.fallback`.
6. Posted as `pr-runtime[bot]` with a per-installation token (writes serialized per installation, one 1.5s gap per task for the review + check pair, backoff on secondary rate limit): inline comments at `{path, line}` with severity, claim, evidence; verdict; the Check run completes `failure` on blocker/major findings, `neutral` on minors, `success` when clean.
7. Metrics (`prr_*`): tasks_pending, runners_idle/busy, reviews_in_flight, admission_rejects_total, cold_assignments_total, tasks_lost_total{reason}, phase_seconds{phase}, wait_for_runner_seconds, time_to_comment_seconds, llm_tokens_total, cost_usd_total{kind}.

---

## 3. Components (as built)

### 3.1 `infra/` (Terraform)
- `vpc.tf` 2 AZs, private subnets for nodes, single NAT. `eks.tf` EKS 1.34, Graviton node group 3× m7g.large with a pinned AMI release, addons (vpc-cni with network policy, coredns, kube-proxy, pod-identity-agent, ebs-csi). `queue.tf` SQS + DLQ. `apigw.tf` HTTP API + `lambda/ingress.py` (HMAC for the App's webhook; PR + mention filtering). `ecr.tf` controller / runner / tools repos. `identity.tf`, `identity-addons.tf` Pod Identity roles (controller, KEDA, baseline job, EBS CSI). `secrets.tf` `pr-runtime/app` seeded from `.env`. `karpenter.tf` module IAM, interruption queue, discovery tags. `driver.tf` driver instance, access entry, SG rule into the cluster SG.
- Helm releases are applied from the CLI (`make rollout`, `deploy/monitoring/kps-values.yaml`, KEDA, Karpenter), not from Terraform.
- `terraform destroy` plus `helm uninstall` and Karpenter NodeClaim cleanup leaves nothing billable.

### 3.2 `controller/` (Python 3.12, FastAPI)
- `bootstrap.py` pull secrets from Secrets Manager before settings load. `config.py` all knobs (cap, review workers, deadline, models, effort, prices, pool size, guidelines dir).
- `queue.py` SQS consumer (PR events and `@pr-runtime` mentions), repo allowlist and per-repo `mode:`, dedupe, supersede, Check run start. `scheduler.py` ranking, admission (cap = Ready runners), assignment, deadline + lost-pod requeue, orphan sweep, result handling, review + post + Check finish, reconcile on startup. `k8s.py` runner pod listing/deletion.
- `reviewer.py` repo-agnostic prompt per config, structured outputs, `read_file` / agentic tool loop. `repo.py` bare git mirror for `read_file`/`grep`/`list_dir`. `github_app.py` JWT → per-installation tokens (cached). `github.py` reviews, Checks API, reactions, comments; PAT fallback with self-review downgrade; serialized writes with rate-limit backoff.
- `db.py` SQLAlchemy models `tasks`, `findings`, `feedback`; `create_all` + idempotent `ALTER` migrations. `api.py` `/api/tasks`, `/api/tasks/{id}`, `/api/tasks/{id}/rerun`, `/api/findings/{id}/feedback`, `/api/stats`, `/api/events` (SSE). `main.py` wiring, `/metrics`, `/eval/results/latest/*` from a ConfigMap, SPA catch-all.
- Image: multi-stage Dockerfile (node builds the console → python stage with git).

### 3.3 `runner/` (Python 3.12, stdlib HTTP server)
- Image: `python:3.12-slim` + git + uv + ruff; the seed repo (Flask fork) cloned to `/opt/seed/flask` with test deps baked in. On boot the seed is staged into the `/work` emptyDir (root fs is read-only).
- `POST /task {repo, clone_url, shas}` → seed repo reused warm (install skipped), any other repo partial-cloned → fetch/checkout/diff → toolchain from `.pr-runtime.yml` or detection (uv/pip/npm/go; none → diff-only review) → install, tests, lint, touched files → `POST /result` → state `done`, `/healthz` returns 503 so it is never reassigned. `RUNNER_MODE=job` + `TASK_FILE` for the KEDA baseline.

### 3.4 `eval/`
- `build_corpus.py` historical reverts (red keeps tests; green reverts tests in a `bug-base/<sha>` branch so the PR diff is source-only) + 20 clean replays (base = main minus C, head = main). `mutate.py` single-operator AST mutants (cmp/boolop/not/offby1/guard/bool/swap_args/del_stmt), green and red, plus `--noisy` (mutant cherry-picked on top of a clean-base so the diff carries a real upstream change). `mutate_xfile.py` cross-file default flips (negative result on Flask, kept).
- `mine_reviews.py` + `distill_guidelines.py` → `eval/guidelines/*.md` (44 rules with PR links) for the `guided` config.
- `run_eval.py` opens PRs (burst), waits for the default config, fires other configs as bursts; `--reuse-prs`, `--accept-existing`, `--only`, `--limit`. `score.py` strict/semantic/FP, per-variant and historical-vs-synthetic breakdown, equivalent-mutant exclusion, cost columns, `--judge`. `combine.py` merges runs into `results/combined/` for the console.
- Outputs per run: `raw.json` (gitignored), `rows.json`, `summary.json`, `summary.md` (committed).

### 3.5 `console/` (React 18 + Vite + TypeScript + Tailwind)
- Tasks (live table via SSE, state/PR filters, phase bar with queue-wait segment, cold/warm dot, cost column, stats strip with pool $/hr), Task detail (findings with feedback, diff viewer, pytest/ruff, re-run with config select), Eval (summary table + recall bars from the published ConfigMap).

### 3.6 `deploy/`
- `chart/` our Helm chart: controller (SA + RBAC + Service + Deployment + ServiceMonitor), runner pool, **KEDA ScaledObject for the runner pool** (`runner.autoscale.*`: prometheus trigger `max(prr_tasks_pending)`, threshold 1/runner, 4..16 replicas, 120s scale-down window, 4 pods/min), NetworkPolicy, Postgres StatefulSet; values pin models, cap (0 = follow the pool), review workers, pool size, prices, secrets id. `baseline/` KEDA ScaledJob (cold-start baseline, disabled). `karpenter/` EC2NodeClass + NodePool. `monitoring/` kube-prometheus-stack values.

### 3.7 `dashboards/` Grafana JSON: pending / busy / idle / in-review stats, queue+pool+admission, rejects and cold assigns, time-to-comment p50/p95, wait-for-runner, phase p50, throughput and spend, runner pod counts.

### 3.8 `docs/` `ONBOARDING.md` (k8s/AWS/networking primer, read first), `DECISIONS.md` (the Q&A study guide, ~30 entries), `TALK.md` (outline, demo script, hard questions, numbers).

### 3.9 Driver box + `scripts/`
- `scripts/driver.sh '<cmd>'` runs on the driver via SSM (base64 transport; `--bg`/`--get`). `driver_env.sh` materializes `.env` from Secrets Manager and logs `gh` in. `Makefile`: `build-controller`, `build-runner`, `push`, `rollout`, `eval-env`.
- `create_webhook.sh` (no-App fallback: per-repo webhook), `sync_secret.sh` (Postgres password), `open_prs.py N` (burst via new PRs), `burst_rerun.py` (burst via reruns, with sampling), `publish_eval.sh`, `publish_guidelines.sh`, `smoke_rerun.sh`.

---

## 4. Demo scenario (what the video shows)

1. Terminal A: `kubectl get pods -n pr-runtime -w`. Four idle runners visible.
2. Terminal B: push a green-test bug branch, `gh pr create`. Tail controller logs: queued → assigned (warm, <1s) → result (~4s) → LLM (~15s) → posted.
3. Browser: PR shows the inline comment at the reverted hunk. Switch to the console task detail: phase bar, finding, thumbs-up.
4. Terminal B: `scripts/open_prs.py 20`. Twenty PRs open in ~5 seconds.
5. Console task list fills live. Grafana: pending spikes, busy pins at cap 4, idle refills in waves, in-review climbs, p95 line. Optional: scale the pool to 16 and show Karpenter bring a spot node in ~40s.
6. Cut to numbers slide (cold 35s vs warm 24s quiet vs 68s p50 under a 56-PR burst; four-config eval table; cost per PR).

Target under 4 minutes. Record 3+ takes; keep raw screen recordings.

---

## 5. Talk outline (~12 min)

1. **The question** (1 min): running untrusted PR code per-task on k8s — what does the warm pool and admission layer actually buy?
2. **Context** (1 min): Uncountable's post, Circus shape. "I wanted to measure it myself."
3. **Architecture** (2 min): the diagram above. Trust split called out. Console = product surface, Grafana = runtime health.
4. **Demo video** (4 min).
5. **Numbers** (2 min): cold vs warm; time-to-comment under burst; where time goes per stage; cost per PR compute vs tokens.
6. **Eval** (1.5 min): four configs, historical vs synthetic, FP on real merged PRs; "context beyond the diff did not measurably help here."
7. **What surprised me / what I'd change** (0.5 min): from `DECISIONS.md`.

Hard questions and prepared answers: `docs/TALK.md`.

---

## 6. Schedule — actual

| Planned | Actual |
|---|---|
| Oct 7: spec, Terraform | Oct 7 evening: spec, Terraform, cluster up, runner image, KEDA baseline, Postgres, Grafana, controller, warm pool, burst test, hardening verified, console, eval plumbing. |
| Oct 8–11: controller, reviewer, console, corpus | Oct 7 night: keys landed, reviewer live, eval run 1 (56 PRs × 2 configs), noisy variant, judge. |
| Oct 12: eval, video | Oct 8 overnight: S5, S9, S1, S3 (+ drain test), S2; four-config eval; laptop kernel panic → driver box. |
| Oct 13: mock Q&A | **Remaining:** video (Oct 12, Anthony records, Claude drives), mock Q&A (Oct 13). |

---

## 7. Known constraints and gotchas (see DECISIONS.md for the stories)

- EC2 quotas on a fresh account: on-demand vCPU 5 → 64 granted same day; spot 5 → 96. Spot also needs `AWSServiceRoleForEC2Spot` to exist (created manually).
- AWS profile `personal` uses root credentials. Works; should be an IAM user + MFA.
- API Gateway's SQS integration cannot forward headers into message attributes; hence Lambda.
- A Deployment restarts an exited container in place; hence controller-side pod deletion.
- GitHub forbids APPROVE/REQUEST_CHANGES from the PR author (single identity → COMMENT fallback) and secondary-rate-limits bursts of review creation (serialized posting).
- The LLM stage, not the runners, is the bottleneck under burst; separate concurrency knob. With the pool autoscaled and 32 review workers, the serialized GitHub posting (1.5s gap per write, one identity) is the bottleneck instead (Oct 10). Writes are now bucketed per installation with one gap per task (review + check in one slot), but every test PR lives under one installation, so a single-tenant burst keeps its ~1.5s/task floor.
- Private repos: the runner has no credentials by design, so it cannot clone them; they get the API-diff fallback review (diff + touched files via the installation token, no tests), marked as such in the review body and the Check title. Running their tests would mean a scoped read token in the pod.
- Every review worker holds a DB session for its whole LLM call: the SQLAlchemy pool must be wider than `REVIEW_WORKERS` (it is sized `review_workers + 12`).
- PR diffs are from the merge-base; a base branch that merely branched off main adds nothing to the diff.
- Equivalent mutants exist (3 found); excluded from recall.
- Elastic nodes: the controller once landed on a spot node and got drained with it; control plane and Postgres are now pinned.
- Controller restart: `reconcile()` requeues `running` tasks and resumes `reviewing` ones from Postgres.
- The laptop kernel-panicked under memory pressure from Docker builds + evals; everything heavy now runs on the driver box.
- Secrets Manager values are seeded from `.env` by Terraform and therefore live in local Terraform state.

---

## 8. Secrets (as built)

Source of truth: Secrets Manager `pr-runtime/app` = `{OPENAI_API_KEY, GITHUB_BOT_TOKEN, POSTGRES_PASSWORD, JUDGE_MODEL}`, seeded by Terraform from the local gitignored `.env`.
- Controller: reads it at boot via Pod Identity (`SECRETS_ID` in Helm values). No k8s Secret mounted.
- Postgres: password from the k8s Secret `pr-runtime-env` (`scripts/sync_secret.sh` from `.env`).
- Driver box: `scripts/driver_env.sh` writes `.env` from the secret and logs `gh` in.
- GitHub identity: Anthony's own `gh` token for now (self-review → COMMENT fallback). A bot account or GitHub App is stretch S4.
- Webhook secret: generated once by `scripts/create_webhook.sh` into `.env`, consumed by the Lambda via Terraform, and pasted into the GitHub App's webhook settings so App deliveries verify with the same key. The per-repo webhook itself is deleted; the App delivers.

## 9. Repo layout

```
pr-runtime/
  SPEC.md  README.md  Makefile
  infra/              Terraform (vpc, eks, queue, apigw+lambda/, ecr, identity*, secrets, karpenter, driver)
  controller/         FastAPI control plane + reviewer + API/SSE; multi-stage Dockerfile builds the console
  console/            React + Vite + TS SPA
  runner/             untrusted executor image
  tools/baseline-fetch  init container for the KEDA baseline
  eval/               build_corpus, mutate, mutate_xfile, mine_reviews, distill_guidelines, run_eval, score, combine; corpus/, guidelines/, results/
  deploy/chart        our Helm chart (controller, runner pool, NetworkPolicy, Postgres)
  deploy/baseline     KEDA ScaledJob cold-start baseline (disabled)
  deploy/karpenter    EC2NodeClass + NodePool
  deploy/monitoring   kube-prometheus-stack values
  dashboards/         Grafana JSON
  docs/               CRASH-COURSE.md, PROJECT-WALKTHROUGH.md, ONBOARDING.md, DIAGRAMS.md, DECISIONS.md, TALK.md
  scripts/            driver.sh, driver_env.sh, create_webhook.sh (fallback), sync_secret.sh, open_prs.py, burst_rerun.py, publish_eval.sh, publish_guidelines.sh, smoke_rerun.sh
```

---

## 10. Stretch — status as of Oct 8: S1, S2, S3, S5, S9 done; S4, S6, S7, S8 not started

### S1. Mined per-directory guidelines ("compiled, not written") — DONE
743 upstream review comments → 44 rules across `src/flask/`, `tests/`, `docs/`, each linking its source PRs; loaded per touched directory in the `guided` config. Result: 45/46, identical to `full`, +3.6k tokens/PR. No lift, no noise; this corpus measures defects, guidelines encode conventions.

### S2. Agentic review loop — DONE
`agentic` config: `read_file`/`grep`/`list_dir` served from a bare git mirror inside the trusted controller (nothing from a PR is executed), 15-call budget. Result: 45/46 strict, 43/46 semantic, 2× tokens, 2.5× latency. Cross-file corpus attempt (`mutate_xfile.py`) was a negative result: Flask's suite covers its cross-file defaults.

### S3. Karpenter — DONE
Burst NodePool on top of the fixed floor; 4→16 runners in 49s; spot after creating the Spot service-linked role; drain mid-task → lost-pod requeue, 0 failed. Controller and Postgres pinned to the floor after the drain evicted the controller once.

### S4. GitHub App — DONE 10/10
JWT → installation tokens, `pr-runtime[bot]` identity, `@pr-runtime` mention trigger, Check runs, per-repo `mode:`. Repo webhook + PAT kept as fallback.

### S5. Cost model per PR — DONE
Compute vs tokens per task, standing pool $/hr, in metrics, console, scorer.

### S6. gVisor runtime class — not started (~half day)
The real answer to "is this a sandbox"; measure the pytest slowdown.

### S7. Live-instance screenshots in the review — not started (~half day)
Mirror Rover's "screenshots from a live instance of its own branch."

### S8. Multi-repo — DONE 10/10
Clone on demand, toolchain detection, `.pr-runtime.yml` overrides, generic prompt. Measured on pallets/click: 18.7s cold vs ~4s warm. Private repos and node/go binaries not done.

### S9. Secrets Manager + Pod Identity — DONE
Controller boots from `pr-runtime/app`; no k8s Secret mounted into it.
