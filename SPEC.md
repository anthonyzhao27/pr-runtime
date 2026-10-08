# pr-runtime — Spec

**One-line:** A pod-per-PR code review runtime on EKS. Every pull request gets its own ephemeral, secret-less pod that clones the branch and runs the test suite; a trusted controller admits work from a queue, keeps a warm pool so nothing waits on a cold start, asks an LLM to review the diff with the test evidence attached, and posts inline review comments back to GitHub. An eval harness with injected real-world bugs measures whether the reviewer catches what CI misses.

**Why this exists (the talk's framing):** Uncountable's engineering post describes Circus: ingress → control plane that ranks and admits → warm pod pool → ephemeral pod per task → egress, with Rover reviewing every PR. The question this project answers for myself: *what does it actually cost to run untrusted PR code per-task on Kubernetes, and what does a warm pool / admission layer buy you under burst?* The reviewer is the workload. The measurements are the deliverable.

**Dates:** Oct 7 build start → **Oct 13 video recorded** → Oct 14 travel → Oct 15 onsite (30 min: ~12 talk, ~18 Q&A).

---

## 1. Decisions (locked)

Each row: what we chose, what we rejected, and the one-sentence Q&A answer.

| # | Decision | Chosen | Rejected | Q&A line |
|---|---|---|---|---|
| 1 | Target repo | Fork of `pallets/flask` | Own toy repo; SQLAlchemy | "Real code I didn't write, ~480 tests in under a minute, and you read Flask daily so you can judge whether a catch is good." |
| 2 | Ingress | GitHub webhook → API Gateway → SQS | ALB Ingress into cluster; polling | "Nothing in the cluster is public. The queue is both the burst buffer and the admission signal." |
| 3 | Control plane | Custom Python controller (`kubernetes` client) | Pure KEDA ScaledJob | "KEDA gives pod-per-task in 20 lines but no warm pool and no ranking. I built KEDA first as the cold-start baseline, then the controller." |
| 4 | Warm pool mechanism | Runner `Deployment` of N idle pods; controller assigns via HTTP to pod IP; runner **exits after one task**; ReplicaSet refills | Long-lived workers reused across tasks; Jobs | "Exit-after-one keeps pods ephemeral per task, and the ReplicaSet is the pool refill logic for free." |
| 5 | Admission | Cap M concurrent busy runners; rank pending by diff size (small first) | FIFO | "Small PRs first minimizes median time-to-comment under burst; the queue absorbs the rest." |
| 6 | Trust boundary | **Split.** Runner pod has zero secrets, clones public fork anonymously, runs tests, POSTs results back. Controller holds all secrets, calls LLM, posts review. | Runner does everything | "A malicious `conftest.py` in a PR can exfiltrate anything the pod can see. So the pod sees nothing." |
| 7 | Pod hardening | `runAsNonRoot`, `readOnlyRootFilesystem` + `/work` emptyDir, `automountServiceAccountToken: false`, drop all caps, CPU/mem limits, controller-enforced deadline, NetworkPolicy egress → github.com + controller only | Default pod spec | "Not a sandbox. gVisor or Firecracker is the real answer; this is the cheapest layer that stops the obvious attack." |
| 8 | LLM step | Single structured call (JSON schema) with diff + full touched files + pytest/ruff output; one tool `read_file` capped at 5 calls | Full agentic loop | "Single call keeps eval stable. `read_file` buys most of the cross-file catching." |
| 9 | Output | Inline review comments via GitHub Reviews API + `APPROVE` / `REQUEST_CHANGES` verdict | One summary comment | "file:line precision is the whole point; a blob comment hides whether it actually found the bug." |
| 10 | Test selection | Full `pytest` every time | Affected-tests only | "Suite is under a minute. Selection logic is a bug farm I didn't need." |
| 11 | Models | Reviewer: gpt-astra. Judge: gpt-luna. Exact IDs pinned in config and logged per run. | Same model for both | "Judge is a different model so the reviewer isn't grading itself." |
| 12 | Eval ground truth | 30 reverted historical Flask bug fixes (two variants) + 20 real clean PRs; strict and semantic "caught" definitions; 2-config ablation | Hand-labeled synthetic bugs | "Bugs are ones Flask maintainers actually shipped and fixed. The interesting variant is the one where CI stays green." |
| 13 | IaC | Terraform (`terraform-aws-modules/eks` + SQS + API GW + IRSA) | eksctl | "One repo describes everything, one `destroy` tears it down. eksctl leaves the non-EKS half orphaned." |
| 14 | Nodes | Managed node group, on-demand, **Graviton (arm64)** since the laptop is arm64 and images build natively | Spot; Karpenter; x86 | "Recording reliability beat saving thirty cents an hour. Karpenter is the real answer at scale; I can talk about preemption from Slurm." |
| 15 | Observability | kube-prometheus-stack via Helm; controller `/metrics`; Grafana dashboard JSON committed; Rich live table in logs for dev | CloudWatch custom metrics | "Grafana at 5s refresh is what the burst video shows; CloudWatch refreshes once a minute." |
| 16 | Demo | Pre-recorded video, cluster may be down on Oct 15 | Live demo | "Travelling the night before. Zero live risk." |
| 17 | Secrets | k8s Secret from gitignored `.env`, mounted into controller only | Secrets Manager + IRSA | "k8s Secret for a 6-day build; in prod this is Secrets Manager via IRSA so nothing is ever on a laptop. That swap is ~30 minutes." |
| 18 | Framing | Infra-question framing with eval as second act. Name Circus on slide 2. | "I built an AI code reviewer" | "You wrote about X. I wanted to measure Y for myself." |

---

## 2. Architecture

```
GitHub (anthonyzhao27/flask fork)
   │  pull_request webhook (opened / synchronize)
   ▼
API Gateway (HTTP API) ──► SQS  pr-runtime-tasks  (+ DLQ)
                              │
                              │ long-poll
                              ▼
                     ┌─────────────────────┐
                     │  controller (pod)   │  trusted: k8s Secret (env)
                     │  - validate HMAC    │  (github-bot PAT, openai key, webhook secret)
                     │  - rank + admit     │
                     │  - assign to runner │
                     │  - LLM review       │
                     │  - post GH review   │
                     │  - /metrics         │
                     └──────┬──────────────┘
                            │ POST /task  {repo, sha, base, pr}
                            ▼
                ┌──────────────────────────────┐
                │ runner Deployment, replicas=N│  untrusted: no secrets, no SA token
                │  warm: Flask pre-cloned,     │  NetworkPolicy: egress github.com + controller
                │        deps pre-installed    │
                │  on task: fetch sha, diff,   │
                │           pytest, ruff,      │
                │           POST results back, │
                │           exit(0)            │
                │  ReplicaSet refills ──►      │
                └──────────────────────────────┘
                            │
                            ▼
                Prometheus ◄── scrape ──► Grafana
```

**Flow for one PR:**
1. Webhook → API GW → SQS message `{pr_number, head_sha, base_sha, action}`.
2. Controller polls SQS, verifies HMAC (signature forwarded as message attribute), fetches diff stats via GitHub API, enqueues in internal priority queue keyed by diff size.
3. Admission loop: while `busy < M` and pending non-empty, pop smallest, pick an idle runner (label `state=idle`), POST task to its pod IP, mark busy, start deadline timer.
4. Runner: `git fetch origin <sha>`, `git checkout`, `git diff base..head`, `pytest -q -x --tb=short`, `ruff check`, POST `{diff, test_output, lint_output, touched_files_content, timings}` to controller `/result/<task_id>`, exit 0.
5. Controller: build prompt (diff + touched files + test/lint output), call reviewer with JSON schema, allow up to 5 `read_file` tool calls served from the runner's result payload (full touched files) or a fresh anonymous fetch via GitHub contents API.
6. Controller posts a GitHub Review: inline comments at `{path, line}` with `severity`, `claim`, `evidence`; verdict `APPROVE` or `REQUEST_CHANGES`.
7. Metrics: `tasks_pending`, `runners_idle`, `runners_busy`, `admission_rejects_total`, `task_duration_seconds{phase=clone|test|llm|post}`, `time_to_comment_seconds`, `cold_start_seconds` vs `warm_start_seconds`.

---

## 3. Components

### 3.1 `infra/` (Terraform)
- VPC (2 AZs, private subnets for nodes, NAT), EKS cluster (1.31+), managed node group: 2× `m7g.large` on-demand → grows to 3 when quota lands.
- SQS queue + DLQ. API Gateway HTTP API with SQS integration (`SendMessage`), forwards `X-Hub-Signature-256` as a message attribute.
- IRSA roles: `controller` (sqs:ReceiveMessage/DeleteMessage), `runner` (none; no SA token mounted).
- EKS addon: VPC CNI with network policy enabled. Helm releases via Terraform `helm_release`: kube-prometheus-stack, KEDA (baseline only).
- `terraform destroy` must leave nothing billable.

### 3.2 `controller/` (Python 3.12, FastAPI + asyncio)
- `ingress.py` SQS long-poll, HMAC verify, dedupe by `(pr, sha)`.
- `scheduler.py` priority queue (diff size), admission cap `M`, idle-runner selection via pod label watch, deadline enforcement (kill pod via API on timeout).
- `reviewer.py` prompt assembly, OpenAI structured outputs, `read_file` tool loop (cap 5), config-pinned model IDs.
- `github.py` Reviews API, review comment posting, verdict.
- `metrics.py` Prometheus client; Rich live table when `DEV=1`.
- Config: `M`, pool size `N`, deadline seconds, model IDs, repo slug.

### 3.3 `runner/` (Python 3.12, tiny HTTP server)
- Image: `python:3.12-slim` arm64 + git + Flask fork cloned at `/work/flask` + `pip install -e .[dev]` baked in. Pre-warmed `.pytest_cache`.
- `POST /task`: fetch, checkout, diff, pytest, ruff, read touched files, POST result to controller, `os._exit(0)`.
- `GET /healthz`. Labels itself `state=idle` on boot via downward API + controller patch (runner has no k8s API access; controller patches labels based on `/healthz` readiness).
- Pod spec: see Decision 7.

### 3.4 `eval/`
- `build_corpus.py`: scan upstream Flask `git log --grep=fix -i` for commits touching ≤3 non-test files that also add/modify a test. For each, create two branches on the fork:
  - `bug/<sha>-redtest`: revert src hunk, keep new test → CI red.
  - `bug/<sha>-greentest`: revert src hunk, drop the test → CI green. **This is the variant that matters.**
  - Record ground truth `{file, hunk_lines, upstream_fix_sha, description}`.
- `clean_prs.py`: replay 20 real merged upstream PRs as branches → negatives.
- `run_eval.py`: open PRs on the fork in batches (this is also the burst), collect reviews, score.
- `score.py`:
  - **strict caught**: a finding in same file and within ±15 lines of the reverted hunk.
  - **semantic caught**: judge model says finding describes the same defect (yes/no, with the upstream commit message as reference).
  - **false positives**: `REQUEST_CHANGES` rate and findings-per-PR on clean PRs; hand-label ~40 findings for precision estimate.
  - **Ablation configs**: `diff_only` vs `diff+tests+files`. One bar chart.
- Output: `eval/results/<run_id>/{raw.jsonl, summary.md, chart.png}`.

### 3.5 `dashboards/`
- Grafana JSON: queue depth, idle/busy runners, admission rejects, time-to-comment p50/p95, cold vs warm start histogram.

### 3.6 `docs/`
- `DECISIONS.md` — living log, appended as things break. This is the Q&A study guide.
- `TALK.md` — slide outline + demo script + hard-questions list.

---

## 4. Burst / demo scenario (what the video shows)

1. Terminal A: `kubectl get pods -n pr-runtime -w`. Four idle runners visible.
2. Terminal B: `eval/open_pr.py --branch bug/<sha>-greentest`. One PR opens.
3. Terminal A: one runner flips busy; a fresh pod schedules to refill.
4. Terminal B tails controller logs: assigned → clone 2s → pytest 38s (green) → LLM 9s → review posted.
5. Browser: PR shows inline comment on the exact line with the reverted hunk, `REQUEST_CHANGES`.
6. Terminal B: `eval/run_eval.py --config full --batch 20`. Twenty PRs open in 5 seconds.
7. Grafana: `tasks_pending` spikes to 20, `runners_busy` pins at M=4, pool refills in waves, p95 time-to-comment visible. Cold-start baseline (KEDA, recorded earlier) shown side by side.
8. Cut to results slide.

Target length: under 4 minutes. Record 3+ takes. Keep raw screen recordings.

---

## 5. Talk outline (~12 min)

1. **The question** (1 min): running untrusted PR code per-task on k8s — what does the warm pool and admission layer actually buy?
2. **Context** (1 min): Uncountable's post, Circus shape. "I wanted to measure it myself."
3. **Architecture** (2 min): the diagram above. Trust split called out.
4. **Demo video** (4 min).
5. **Numbers** (2 min): cold vs warm p50/p95; time-to-comment under burst with M=2/4/8; cost per PR split into compute vs tokens.
6. **Eval** (1.5 min): recall on red-test vs green-test bugs; FP rate on clean PRs; ablation chart.
7. **What surprised me / what I'd change** (0.5 min): filled from `DECISIONS.md`.

**Hard questions to prepare** (expand in `TALK.md` as they come up):
- Why not gVisor / Firecracker / Kata? What does your NetworkPolicy *not* stop?
- How does the controller know a runner is idle? What if the controller dies mid-task?
- What happens on `synchronize` (new push to same PR) while a review is in flight?
- Why exit-after-one instead of reusing pods? What does it cost?
- Why diff-size ranking? Starvation of big PRs?
- Why SQS over NATS? (Circus uses NATS.)
- Why on-demand not spot? How would you handle a spot interruption mid-test?
- How did you pick ±15 lines? Judge agreement with your hand labels?
- What's the LLM's false positive rate on clean PRs and would you merge on APPROVE?
- What's the per-PR cost? Where's the money going?
- Why Python for the controller when Circus is TypeScript?
- What would you need to make this multi-repo?

---

## 6. Schedule (Oct 7 → Oct 13)

Stretch items are *not* on this schedule. They happen only if a day finishes early.

| Date | Deliverable | Done when |
|---|---|---|
| **Oct 7 (Tue)** | Spec, repo skeleton, Terraform written and `apply` started. Quota increase requested. | `kubectl get nodes` shows nodes. eksctl bailout if Terraform >4h. |
| **Oct 8 (Wed)** | Runner image built + pushed (ECR). KEDA ScaledJob baseline: webhook → SQS → Job → pytest output in logs. Cold-start numbers recorded. | Open PR on fork → Job runs → logs show test output. |
| **Oct 9 (Thu)** | Controller: SQS poll, warm pool, assign, exit-after-one refill, admission cap, Rich table. No LLM yet. | 10 PRs in a burst → all run, cap respected, pool refills. |
| **Oct 10 (Fri)** | Reviewer: structured LLM call + `read_file`, inline GitHub review posted as bot. Trust split + pod hardening + NetworkPolicy. | PR gets inline comments from bot account. Runner has no secrets. |
| **Oct 11 (Sat)** | Eval corpus built (30 bugs × 2 variants + 20 clean). Judge. Prometheus + Grafana + dashboard. Run eval, both configs. | `summary.md` + chart exist. Grafana shows burst. |
| **Oct 12 (Sun)** | Record video (3+ takes). Slides. `DECISIONS.md` finalized. | Video file + deck exist. |
| **Oct 13 (Mon)** | Anthony runs the demo end-to-end himself. 30-min mock Q&A. `TALK.md` hard questions answered. Optional `terraform destroy`. | Anthony can answer every hard question without notes. |

---

## 7. Known constraints and gotchas

- **EC2 on-demand vCPU quota is 5 in us-east-1.** Increase to 32 requested Oct 7 (request `9c866f461a7d420e8c3cc67bea590b15sI75Z0QF`). Until approved: 2× `m7g.large` = 4 vCPU. Pool N=3, cap M=3. Spot quota is also 5.
- **AWS profile `personal` uses root credentials.** Works, but should be an IAM user with admin + MFA. Not blocking; flagged.
- **Laptop is arm64.** Nodes are Graviton (arm64) so images build natively. If x86 ever needed: `docker buildx --platform linux/amd64`.
- **Free-token OpenAI org rate limits** unknown. Eval = ~50 PRs × 2 configs × ~20k tokens ≈ 2M tokens at concurrency ≤4. Verify limits Oct 10 before eval day.
- **GitHub webhook on a fork**: webhooks are per-repo, fine. Bot account must be added as collaborator on the fork to post reviews.
- **`synchronize` events**: dedupe by `(pr, head_sha)`; a new push cancels the in-flight task for that PR (kill runner pod, mark superseded).
- **Controller restart**: in-flight tasks are lost. SQS visibility timeout (10 min) means unacked messages reappear. Acceptable; documented.

---

## 8. Secrets setup (Anthony)

Put both values in `~/pr-runtime/.env` (gitignored). A script turns it into a k8s Secret mounted only into the controller pod. The runner never sees it.

```
OPENAI_API_KEY=sk-...
GITHUB_BOT_TOKEN=github_pat_...
```

GitHub bot account:
1. Create a new GitHub account (e.g. `pr-runtime-bot`). Needs a fresh email.
2. Settings → Developer settings → Fine-grained PAT. Resource owner: the bot. Repository access: only `anthonyzhao27/flask`. Permissions: Pull requests: Read and write. Contents: Read. Metadata: Read.
3. From `anthonyzhao27`: add the bot as a collaborator on `anthonyzhao27/flask`; accept from the bot.

Webhook secret is generated by `scripts/create_webhook.sh` and written to the same `.env`.

## 9. Repo layout

```
pr-runtime/
  SPEC.md
  infra/              Terraform
  controller/         FastAPI controller, Dockerfile
  runner/             runner server, Dockerfile (Flask baked in)
  eval/               corpus builder, runner, scorer, results/
  dashboards/         Grafana JSON
  deploy/             k8s manifests / Helm chart for controller + runner + NetworkPolicy
  docs/DECISIONS.md   living decision log
  docs/TALK.md        slides outline, demo script, hard questions
  scripts/            create_webhook.sh, open_pr.py, burst.py
```

---

## 10. Stretch (only after §6 is green; in priority order)

Each item is independently droppable. None are referenced by the core schedule.

### S1. Mined per-directory guidelines ("compiled, not written") — ~half day
Pull the last ~300 review comments from upstream `pallets/flask` PRs via the GitHub API. One LLM call per top-level directory: distill into ≤15 checkable rules, each tagged with the source PR URLs. Write `GUIDELINES.md` per directory; reviewer loads the ones for touched dirs. Adds a third ablation bar. Talk line: toy version of "3,744 standards, each traceable to its origin." Fallback: hand-write 10 rules and label the slide honestly.

### S2. Agentic review loop — ~1 day
Replace the single call + `read_file` with a bounded tool loop: `read_file`, `grep`, `list_dir`, `run_tests(path)` executed in the runner (requires keeping the runner alive until the LLM finishes, which changes the trust split: runner would need a controller-issued short-lived token to accept follow-up commands). Cap 15 tool calls / 3 min. Compare recall and latency against the single-call config as a fourth ablation bar. Talk line: when does exploration beat context stuffing.

### S3. Karpenter instead of fixed node group — ~1 day
NodePool with Graviton on-demand + spot, consolidation on. Pool refill then also scales nodes under burst. Needs IRSA for Karpenter, interruption-queue handling, and a spot-interruption test (drain mid-pytest, task requeued). Talk line: preemption handling, same problem as Slurm spot nodes.

### S4. Dedicated bot identity polish — ~1 hour
GitHub App instead of PAT: reviews appear as an app with an avatar; installation token per repo; webhook delivered by the App (drops the per-repo webhook script). Multi-repo becomes one install click.

### S5. Cost model per PR — ~2 hours
Compute (node-seconds × price) + tokens (input/output × price) per PR, exported as a metric and a table in results. Talk line: "where does the money go: tests or tokens?"

### S6. gVisor runtime class — ~half day
EKS with `runsc` RuntimeClass on a dedicated node group (needs custom AMI or bottlerocket with gVisor). Runner pods use it. Measures the pytest slowdown under gVisor. Talk line: the real answer to "is this a sandbox."

### S7. Live-instance screenshot in review — ~half day
Runner starts the Flask app from the PR branch and hits a few endpoints, attaches responses/screenshots to the review. Mirrors Rover's "four screenshots from a live instance of its own branch."

### S8. Multi-repo — ~half day
Repo slug from webhook payload; runner image per repo or generic image with clone-on-boot (loses warm clone, measure the cost). Shows what the warm pool assumption actually buys.

### S9. Secrets Manager + IRSA — ~30 min
Replace the k8s Secret with `secretsmanager:GetSecretValue` at controller boot via an IRSA role. Nothing secret ever on the laptop or in etcd. Talk line: "this is the prod answer; k8s Secret was the 6-day answer."
