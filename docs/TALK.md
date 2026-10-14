# Talk: pr-runtime (Uncountable onsite, Oct 15, internship)

**Presentation slot: 30 minutes, Q&A included.** Talk for **13 to 16 minutes, video included**, and leave the rest for questions. Never go past 18. Expect intros and interruptions to take 3 to 5 minutes. When an interruption happens, answer it and pick up where you were. The demo and the scaling slide are the two things that must happen.

**Separately: 20 minutes with the founders.** You ask the questions. See [Questions to ask the founders](#questions-to-ask-the-founders).

The audience built Circus. They aren't checking whether you can out-build them. They're checking whether you can explain what you made, whether you understand your own decisions, whether you measured honestly, and what you'd do next.

---

## The 60-second version (memorize this)

> PR review is the bottleneck at my startup. Katie walked me through Circus: every agent task in its own pod from a warm pool. I'd never thought about review as an orchestration problem, so I learned Kubernetes and built the review half of it.
>
> It's a GitHub App. When a PR opens, or someone tags `@pr-runtime`, the event goes through API Gateway and a Lambda that checks the signature, into SQS. A controller on EKS leases the task and hands it to a warm, throwaway pod. The pod clones the repo, runs the tests and linter, and is deleted. The controller sends the diff and the test results to a model, then posts inline comments and a Check run as the bot. It takes about 20 seconds from PR open to review.
>
> The pool scales from the queue. KEDA reads SQS and sets the pod count. Karpenter adds a spot node when the pods don't fit. Under a burst, the pods were never the bottleneck. Scaling them exposed the model, then GitHub's rate limit.

---

## Deck (8 slides, ~13 min; the extra 3 min for a 16-min talk go to slides 3, 4 and 7)

| # | Slide | Time | What's on it | Say |
|---|---|---|---|---|
| 1 | Title | 0:20 | "pr-runtime: a GitHub App that runs and reviews every PR in its own pod." | One sentence. Don't ask a question here. |
| 2 | Why I built this | 1:00 | Your bullets (below) | The three beats below, plus the agent line. |
| 3 | How it works | 2:00 | System diagram (`docs/images/slide-system.png`) | Walk the six numbered arrows. One sentence on "nothing in the cluster is public." |
| 4 | Scaling the pool | 1:30 | `docs/images/autoscale-loop.png` | The scaling story (below). |
| 5 | Demo | 3:30 | Video | Narrate (`~/pr-runtime-demo/SCRIPT.md`). |
| 6 | Numbers | 1:30 | 6 big numbers (below) | Say the burst story aloud; it isn't printed. |
| 7 | Tradeoffs | 2:00 | Chose / over / because table (below) | For each row, say where it bit you, if it did. |
| 8 | What's next | 0:45 | 4 bullets + repo link | End with "That's it, questions." |

Slide rules: a title plus one visual, or up to 4 one-line bullets. Numbers appear only on slide 6. Hidden backup slides go after slide 8 (see the checklist).

### Slide 2: Why I built this

Your bullets:
- Genuine issue in work at my startup
- We move fast, so the bottleneck becomes actually reviewing code
- Inspired by Circus + Rover
- Same problem, solved by orchestration
- Good way to learn Kubernetes for production
- I've always been on the training side of infra (Slurm)

Say: "PR review is our bottleneck. Katie described Circus, where every agent task gets its own pod from a warm pool. Review was the task I needed, so that's the piece I built. I come from Slurm, which is batch training infra, so Kubernetes in production was my gap."

Agent line, said here, before anyone asks: "A coding agent wrote most of the code. I set the constraints, reviewed by breaking things, and made every call. There are 36 dated entries in the decisions log."

### Slide 3: How it works

The diagram has six arrows: (1) webhook, (2) lease a message, (3) repo + SHA to a pod, (4) test result back, (5) diff + evidence to OpenAI, (6) inline review + Check as `pr-runtime[bot]`.

Say: "Nothing in the cluster is public. GitHub talks to API Gateway, the Lambda rejects anything unsigned, and the controller pulls from the queue. The runner gets a URL and a SHA and returns JSON. The controller holds every credential and never runs PR code."

### Slide 4: Scaling the pool

| Layer | Watches | Does |
|---|---|---|
| KEDA | SQS: waiting + running | Sets runner pods, 4 to 16 |
| Karpenter | Pods stuck Pending | Adds a Graviton spot node, removes it when idle |
| Controller | Ready pods | Leases only as many tasks as there are Ready pods |

Say:
1. "Three controllers, each with one signal. KEDA reads the queue and sets the pod count. Pods that don't fit go Pending, and Karpenter buys a spot node. The controller leases only what it can run, one task per pod."
2. "The first version scaled on my own metric through Prometheus. I asked what happens if Prometheus dies, and the answer was that the pool stops resizing. I switched to leasing from SQS: take only what you can run, ack when it's done. The queue is the backlog, so KEDA reads the queue. It's the same pattern as an Auto Scaling group on SQS depth. I tested it with Prometheus turned off."
3. "The honest part: more runners didn't make a review faster. A runner takes 4 seconds. Scaling just moved the queue to the model, then to GitHub's rate limit."

### Slide 6: Numbers (big digits, one label each)

| Number | Label |
|---|---|
| **~20s** | PR open → review posted, warm pool (24s p50 over 20 quiet PRs; 17.6s in the Oct 13 dry run) |
| **35s → 4.5s** | cold Job per PR vs a warm pod, time to test result |
| **8s / 40s** | KEDA asks for 16 pods / spot node Ready |
| **97.8%** | 46 planted bugs caught, 0 false positives on 20 clean PRs |
| **$0.04–0.26** | tokens per review across four context configs, same recall |
| **4s** | runner time; never the bottleneck |

Spoken, not printed: the 56-PR burst p50 went from 70s (fixed pool) to 165s (autoscaled), because GitHub posting became the queue. Posting as the App brought it to 81s.

### Slide 7: Tradeoffs

| Chose | Over | Because | Where it bit me |
|---|---|---|---|
| Delete the pod after each PR | Reuse it, or restart the container | Each PR runs untrusted code and needs a clean sandbox. A container restart keeps the pod and its volumes. | v1 exited after one task, and Kubernetes restarted it in the same pod. |
| Pull from SQS behind API Gateway + Lambda | A webhook straight into the cluster | Nothing in the cluster is public. The Lambda rejects unsigned events, and the queue absorbs bursts. | API Gateway can't forward the signature header to SQS, which is why there's a Lambda. |
| Pods on Kubernetes | Lambda per task | Needed a warm pool I control, delete-after-one, and per-pod network policy. Lambda is fine for a diff-only reviewer. | |
| Runner has no secrets, no token | Runner clones with a token | `pytest` runs the PR's code. Private repos get a diff-only fallback review instead. | |
| Fixed on-demand floor; spot only for burst | All spot | Runner tasks are 4s and replayable; a lost spot node costs one retry. | The controller once landed on a burst node and got drained. It's pinned now. |

Autoscaling (lease vs drain, KEDA signal) lives on slide 4, not here.

### Slide 8: What's next

- Let the agent act inside the pod: run, edit, rerun tests, propose a verified patch
- Multimodal review: render frontend PRs headless, send screenshots to the model
- Run it on my startup's repos (private repos need a scoped clone token)
- A harder eval corpus: cross-file bugs, since every config tied on this one

Say for bullet 1: "The hard part isn't the tools, it's the loop budget and the blast radius. The pod is already throwaway, so the blast radius is solved."

---

## Demo video (~3:30, recorded Oct 13, narrated live)

The full click-by-click script is `~/pr-runtime-demo/SCRIPT.md`.

| Beat | Time | On screen | Say |
|---|---|---|---|
| Warm pool | 0:15 | `pods.sh`: 4 Running | "Four warm pods. Each runs exactly one PR, then gets deleted." |
| One PR | 1:15 | Open `demo/session-keys` PR → `logs.sh`: queued / assigned (warm, <1s) / posted → inline comment on `sessions.py`, red Check | "This quietly breaks secret-key rotation. All tests pass." … "The bot flags the exact line. CI's green, this Check is red." |
| Never-seen repo | 0:45 | `@pr-runtime review` on click PR #1 → 👀 → review | "Same thing by mention, on a repo it has never seen. It clones, figures out how to install it, and runs 2,000 tests." |
| Burst | 1:15 | `burst.sh`, then Grafana top row: SQS queue, Runner pods (KEDA), Nodes (Karpenter) | "24 at once. KEDA reads the queue and asks for 16 pods. They don't fit, so Karpenter buys a spot node in about 40 seconds." |

Backup: if the video fails, slides 3 and 4 carry the talk. Hidden slides hold the PR comment and Grafana burst screenshots.

---

## Likely questions and short answers

**System**
1. *Walk me through a PR.* → The 60-second version, six arrows.
2. *Why a queue instead of a webhook?* → Nothing in the cluster is public. The controller pulls, the Lambda already rejected unsigned events, and the queue absorbs bursts.
3. *Why does the runner have no secrets?* → `pytest` doesn't run tests on the PR, it runs the PR: `conftest.py`, module imports and build hooks all execute first. So the pod gets a public URL and a SHA, has no service-account token and a read-only root, and can only reach the controller, DNS and port 443.
4. *How does it clone with no credentials?* → Public repos need none. Private repos get a diff-only review fetched by the controller, labelled "Tests not run."
5. *Why delete the pod?* → It's the sandbox boundary. A container restart reuses the pod and its volumes.
6. *Is it one pod per node?* → No. One task per pod, many pods per node. A runner asks for half a core, so a 2-core node holds about 3. Pods share the node's kernel; that's why gVisor is the next isolation layer.
7. *Why Kubernetes for API calls? Why not Lambda?* → For a diff-only reviewer, Lambda would be fine. Lambda does have a filesystem, `/tmp`, up to 10 GB. But AWS decides when to reuse a warm instance, including its `/tmp`, and I needed delete-after-one plus a pool I control. It's also Circus's shape.
8. *KEDA vs Karpenter?* → KEDA sizes pods from SQS waiting + running. Karpenter sizes nodes from Pending pods. The controller's cap follows Ready pods.
9. *What if Prometheus dies?* → Nothing, since Oct 12. KEDA reads SQS directly; Prometheus and Grafana are for humans. Before that the pool would have frozen. Tested by scaling Prometheus to zero during a burst.
10. *Prometheus vs Grafana vs Postgres?* → Prometheus stores numbers over time, Grafana draws them, Postgres stores one row per task (state, findings, verdict, cost). Lose Prometheus and you get a gap in a graph. Lose Postgres and you break restart recovery and dedupe.
11. *What if a runner dies mid-task?* → The controller sees the pod vanish and requeues it, up to 3 attempts. Tested by draining a spot node: one retry, nothing failed.
12. *What if the controller dies?* → Postgres is the source of truth. On boot it requeues running tasks and resumes reviews (tested with 17 in flight). Leased SQS messages reappear after the 10-minute visibility timeout and get deduped.
13. *Can the bot approve or merge?* → It submits an APPROVE review on clean PRs and REQUEST_CHANGES plus a failing Check on bad ones. It can't merge: its token has `contents: read`, and merging needs write. GitHub enforces that, not my code. Whether a bot approval counts is a branch-protection choice.
14. *Why were PRs still waiting when 15 pods were Ready (Oct 12 burst)?* → Intake was the limit. Each message waited on GitHub writes and the consumer was serial, about 5.5s per message. Parallel handlers brought it to 3.2s. The bottleneck moved again.
15. *Other repos?* → Install the App. The runner clones on demand and detects the toolchain (uv, pip, npm, go) or reads `.pr-runtime.yml`.

**Measurement**
16. *How do you know it's good?* → Planted bugs with known lines, strict and judge-model scoring, clean PRs for false positives. 97.8% recall, 0 false positives. Four context configs tie and differ only in cost.
17. *What surprised you?* → Runners were never the bottleneck, and more context didn't find more bugs.
18. *What did you get wrong?* → The scaling signal depended on Prometheus. Before that, I diagnosed the posting bottleneck wrong. And the eval harness was wrong twice before the model was.

**You**
19. *How much did you write?* → "Not most of the code. I decided what to build, set the constraints, reviewed by breaking things (drains, restarts, Prometheus off), and read the parts you can't test: IAM, network policy, secrets. Four times it mattered: an apply I hadn't authorized, a secret about to be committed, a doc aimed at the wrong audience, and the scaling signal."
20. *Slurm vs Kubernetes?* → Slurm is batch: jobs finish, and the scheduler hands out nodes. Kubernetes is declarative: you declare 4 pods exist and it keeps that true. My warm pool is a Kubernetes idea; a ReplicaSet refills it. Slurm has no equivalent.

---

## Questions to ask the founders

Ask these three for sure:
1. "What does a great intern's summer look like here? What did the best one you've had actually ship?"
2. "Your post says agents do a lot of the coding. What's still hard for humans here, and has that changed who you hire?"
3. "What's something you believed when you started Uncountable that turned out to be wrong?"

If there's time:
- "When an intern disagrees with a design decision, what happens?"
- "What broke first when you scaled Circus, and what's the bottleneck now?"
- "How do you know Rover's reviews are good: measured, or vibes?"
- "What does a materials scientist do differently on the day they start using Uncountable?"
- "What would make you want someone back full-time after the summer?"
- Fun: "What's the most unusual material a customer has used Uncountable for?"

Avoid: funding and valuation, anything that's on the website, and "what's the culture like" in that generic form.

---

## Day-of checklist

- Video on the laptop and on a USB stick. Slides also exported to PDF.
- Hidden backup slides after slide 8: PR review comment, Grafana burst, console task detail, eval table.
- Cluster stays up through the onsite (~$10/day). After that, tear it down on your word.
- If it's live and they ask: `~/pr-runtime-demo/pf.sh`, then Grafana at `localhost:3000`.
- Say the agent line on slide 2, before anyone asks.
- When you don't know: "I didn't measure that. Here's how I would."
- Stop talking at 16 minutes no matter what.

---

## Appendix A: deep questions (skim, don't rehearse)

- gVisor/Firecracker vs containers; what NetworkPolicy can't stop (any host on 443).
- Why SQS over NATS; diff-size ranking and starvation; cap vs pool size.
- Spot: fine for 4-second replayable tasks, wrong for the control plane.
- Pod Identity vs IRSA; EKS access entries; why the driver box needed a security-group rule.
- Merge-base diffs and why the first noisy corpus wasn't noisy.
- Why Terraform rolled every node once (module tracked the latest AMI; now pinned).
- Secondary rate limits; per-installation tokens; check-run ids overflowing int32.
- SQLAlchemy pool starved by 32 review workers.
- Lease vs drain: why the queue read ~0 under the old design, why leasing makes SQS the truth, what the 600s visibility timeout means for a lost controller, and why priority-by-diff-size was given up.

Each has an entry in `docs/DECISIONS.md`.

---

## Appendix B: full numbers log (every run, for reference)

| scenario | n | time-to-comment p50 | p95 | wait for runner p50 | runner | LLM p50 / p95 |
|---|---|---|---|---|---|---|
| cold baseline (KEDA ScaledJob, no pool, no LLM) | 1 | ~35s | — | ~15s (pod schedule) | 6s (+16s pod) | — |
| quiet, warm pool (single PRs) | 20 | 24s | 72s | 0.5s | 3.9s | 14.9s / 25.1s |
| burst (56 PRs, cap 4, pool 4) | 121 | 98s | 224s | 33s | 4.0s | 14.2s / 38.8s |
| burst, fixed pool, `full` only (Oct 7, from Postgres) | 56 | 70s | 200s | 39s | 4.0s | 16.0s / 28.7s |
| burst, autoscaled (KEDA 4→16 + Karpenter spot node, 32 workers; Oct 10) | 56 | 165s | 242s | 40s | 4.1s | 9.3s / 18.0s |
| burst, autoscaled + App posting (per-installation buckets, one gap per task, no self-review retry; Oct 10) | 56 | 81s | 143s | 40s | 4.0s | 8.1s / 16.2s |
| burst, SQS-lease + KEDA on SQS, Prometheus off (24 mentions; Oct 12) | 24 | 18s* | 30s* | 0.6s | 3.6s | 5.4s / 16.3s |
| burst, SQS-lease + parallel intake (12 mentions; Oct 12) | 12 | 24s* | 30s* | ~1s | ~3.6s | — |
| single PR dry run (Oct 13, PR #93) | 1 | 17.6s | — | 0.4s | ~4s | — |

\* Measured from task creation. In lease mode a task row exists only once the message is leased, so time spent waiting in SQS isn't included (intake was ~5.5s/message, then ~3.2s after the fix).

Eval run 1 (gpt-6-astra @ high, judge gpt-6-luna): full = 35/35 strict, 34/35 semantic, FP 0/20; diff_only = 34/35 strict, 34/35 semantic, FP 0/20. One equivalent mutant excluded. Noisy v2 (bug inside a real 2-6 file upstream diff, n=11): full 10/11, diff_only 11/11 strict (10/11 semantic). Configs indistinguishable at this n.
Guided (full + 44 mined guidelines): 45/46, FP 0/20, +3.6k tokens/PR, identical to full. Combined over all 46 bugs: full 97.8%, diff_only 97.8%, guided 97.8%; FP 0/20 for all three.
Cost: ~$0.04–0.06 tokens per average review, $0.21 for a 19k-token one; compute $0.00004; idle pool of 4 = $0.16/hr.
Agentic (grep/read_file/list_dir, 15 calls): 45/46 strict, 43/46 semantic, 24k tokens/PR ($0.26) vs 11k ($0.11) for full, p50 145s vs 58s. Exploration = pure cost on this corpus.
Four-way: diff_only ≈ full ≈ guided ≈ agentic on recall (97.8% strict); they differ only in cost/latency.
Karpenter: pool 4→16, node Ready in 41s, 16 runners in 49s; spot c7g.2xlarge after creating the Spot service-linked role. Drain mid-task: lost pod detected, task requeued, posted on attempt 2 (50s), 0 failed. Oct 13 burst: Karpenter picked two smaller spot nodes (m8g.large + c7g.xlarge) instead of one big one; cheapest fit at that moment.
Multi-repo (pallets/click, never seen): clone 0.4s, install 3.0s, 2,238 tests 14.7s, 18.7s total vs ~4s on the warm Flask seed.
GitHub App: reviews as pr-runtime[bot] (APPROVE on clean PRs, REQUEST_CHANGES otherwise), Check run per task (failure on blocker/major, neutral on minor, success when clean), @pr-runtime mention trigger. Cannot merge: installation token has contents: read.
Tokens: ~4k in / 0.4k out per review. 112 reviews ≈ 0.45M input tokens.
SQS-lease burst (Oct 12, 24 mention messages sent in 0.3s, Prometheus scaled to 0 for the first 120s): HPA asked for 16 at t+8s, NodeClaim at t+16s, 16 runners Ready at t+71s, queue drained at t+118s, pool back to 4 and node gone at t+341s; 24/24 posted, 0 failed; wait-for-runner p50 0.6s. Intake was the limiter (~5.5s/message: the mention path's reaction, check-open, review and check-finish all share the per-installation write bucket with a 1.5s gap, and the consumer was serial), not the pool. Fixed the same night: 8 handler threads, 3.2s/message.
Autoscaled burst (Oct 10): HPA asked for 16 at t+10s, c8g.2xlarge spot node Ready at t+42s, 16 runners at t+78s, runner stage drained at t+86s (vs ~105s fixed); pool back to 4 by t+295s, node consolidated ~t+360s; 0 failed, 0 requeued; burst node ≈ $0.016, tokens $6.04. Time-to-comment got worse (p50 165s vs 70s) because GitHub posting is serialized and became the queue once runners and the LLM stopped metering reviews out: `post` p50 100s. The two writes per task turned out to be the PAT self-review 422 + retry, not the Check.
Posting burst (Oct 10, third run): same 56 PRs posted as the App with a Check each; writes bucketed per installation, one 1.5s gap per task. Time-to-comment p50 81s / p95 143s, post wait p50 23s / p95 61s, last review at t+151s (vs t+253s); 168 writes, none rate-limited; 0 failed. Honest reading: one installation means the buckets could not help here; most of the gain is "no self-review retry as the App" (one write per review instead of two). First attempt deadlocked (GitHub slot held across a Postgres row lock) and was fixed before the measurement. Private repos now get an API-diff fallback review labelled "Tests not run".

Talking points from these: under burst, the runner is never the bottleneck (4s). Queue wait, the LLM stage, GitHub posting and, under leasing, message intake are, each in turn. Autoscaling moved the queue; it did not shorten it. Cold vs warm matters most for the *first* wave; after that it's a throughput question.
