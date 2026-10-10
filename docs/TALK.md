# Talk: "What does it cost to run untrusted PR code per task on Kubernetes?"

30 minutes: ~12 talk, ~18 Q&A. Video pre-recorded. Cluster may be down.

## Slides (draft)

1. **The question.** Agents open PRs faster than humans review them. Every PR wants its own environment: clone, deps, tests, then an LLM reads it. What does a warm pool and an admission layer actually buy under burst, and what does "untrusted" cost?
2. **Context.** Uncountable's post: Circus = ingress → control plane ranks/admits → warm pod pool → ephemeral pod per task → egress; Rover reviews every PR. "I wanted to measure this shape myself, on a codebase you know (Flask)."
3. **Architecture.** Diagram from SPEC §2. Call out the trust split: runner has no secrets, no SA token, egress only to GitHub + controller. Controller holds everything. Console = product surface, Grafana = runtime health.
4. **Demo video (4 min).** Single PR warm path → inline review → console → 20-PR burst → Grafana.
5. **Numbers.** Cold (KEDA ScaledJob) vs warm (pool) p50/p95 time-to-comment. Wait-for-runner under burst at cap 4 / pool 4 vs pool 8. Where time goes per phase (fetch, pytest, LLM, post). Cost per PR: compute vs tokens.
6. **Eval.** Recall on green-test bugs (historical vs synthetic, reported separately), red-test bugs, FP rate on 20 real merged changes. Ablation: diff-only vs diff+tests+files.
7. **What surprised me / what I'd change.** From DECISIONS.md: API GW→SQS header limit; exit-after-one restarts in place; Flask tests are 1s so the pool pre-pays everything; corpus yield; (fill after eval).

## Demo script (video)

Terminal A: `kubectl get pods -n pr-runtime -w` (4 idle runners visible).
Terminal B: push a green-test bug branch, open PR. Tail controller logs: queued → assigned (warm, 0.7s) → result → LLM → posted.
Browser: PR inline comment at the exact line. Switch to console task detail: phase bar, finding, thumbs-up.
Terminal B: `scripts/open_prs.py 20`. Console task list fills live. Grafana: pending spikes, busy pins at cap, idle refills in waves, p95 line.
Cut to numbers slide.

## Hard questions (answer without notes)

**Trust / sandboxing**
- Why not gVisor / Firecracker / Kata? → They are the real answer. This is the cheapest layer: no secrets in the pod, no SA token, read-only root, dropped caps, egress allow-list. A kernel exploit still escapes. gVisor is stretch S6.
- What does your NetworkPolicy *not* stop? → Exfiltration over HTTPS to any host on 443 (we allow 0.0.0.0/0:443 for GitHub). Tightening = resolve GitHub's CIDRs or route clones through an in-cluster proxy. Also DNS tunneling.
- Malicious `conftest.py` reads the controller's token? → It cannot: the token is not in the pod. It could POST a fake result to the controller, so the controller should authenticate results (per-task nonce). Not done; would be next.
- Why does the KEDA baseline pod hold AWS creds? → No dispatcher: the Job has to pull its own SQS message. That is the trust leak the controller removes.

**Scheduling**
- How does the controller know a runner is idle? → Pod Ready (readiness = runner reports idle) and not in the controller's assigned set. Readiness flips to 503 the moment a task is accepted.
- Controller dies mid-task? → In-flight tasks are lost from memory; SQS message was already deleted. DB row stays `running`; a restart could reconcile from the DB (not implemented). SQS visibility timeout protects the un-consumed ones only.
- New push while a review is in flight? → `supersede`: older tasks for the same PR marked superseded, running pod deleted.
- Why exit-after-one? And why did it not work? → Ephemeral per task, dirty workdir never reused. A Deployment restarts the *container* in place, so the controller deletes the pod instead; the ReplicaSet refills.
- Why diff-size ranking? Starvation? → Small PRs first minimizes median wait under burst. Large PRs can starve under sustained load; aging (priority decays with wait) is the fix. Not needed at this scale.
- Why SQS over NATS? → No NATS to run or secure; the queue doubles as the burst buffer and the webhook sink. NATS wins on latency and fan-out when you have many agent types.
- Cap M vs pool N? → M bounds concurrent test runs (node CPU). N bounds how many are pre-warmed. N > M means the next wave does not wait for refill. Measured: N=M=4 → second wave waited ~7s.
- Why on-demand nodes? → Video reliability. Spot interruption mid-pytest = task requeue (attempts<2) and pool shrink; Karpenter + interruption queue is stretch S3.

**Reviewer / eval**
- Why one structured call + read_file, not an agent loop? → Stable to eval, cheap, fast. read_file gets most of the cross-file catching. Loop is stretch S2.
- Judge is a different model than the reviewer? → Yes: gpt-luna judges, gpt-astra reviews. No self-grading.
- Why ±15 lines? → Hunk-local tolerance for findings anchored on a neighboring line; semantic judge is the real metric. Report both.
- How did you build ground truth? → Historical: reverse real upstream fixes onto main; keep tests (red) or revert them too (green). Synthetic: single-operator mutations the suite does not catch. Clean: 20 real merged upstream changes replayed forward. All on the fork.
- Would you merge on APPROVE? → Not at this FP rate / corpus size. Show the FP rate and say what gate you'd add (tests green + no blocker + human for risky paths).
- Where did the money go? → Tokens vs compute per PR (fill from eval).

**Full-stack**
- Why serve the SPA from the controller? → One image, one deployment, same origin, no CORS, no CDN to configure. Separate frontend is a split when the team or deploy cadence demands it.
- What happens to feedback rows? → Nothing yet. They are the raw material for mined guidelines (S1): thumbs-down findings become negative examples per directory.
- Postgres in-cluster: what breaks if the node dies? → PVC is EBS in one AZ; pod reschedules in the same AZ or waits. RDS or Multi-AZ for prod.
- Why Python when Circus is TypeScript? → Python is the backend language here and the runner is Python-shaped (pytest); a TypeScript control plane is a port, not a redesign.

**Infra**
- Why Terraform over eksctl? → One repo for EKS + SQS + Lambda + IAM + ECR, one destroy.
- Why Pod Identity, not IRSA? → No OIDC provider juggling; association is one resource per SA.
- Why Lambda in the ingress path? → API GW's SQS integration can't forward the HMAC header into message attributes (hard limit). Lambda verifies at the edge, so nothing unauthenticated reaches the queue.
- What did you learn from Slurm that applied here? → Admission/cap, preemption handling, and that capacity is a quota problem before it is a scheduler problem (vCPU quota was 5).

## Numbers so far (Oct 7 night; refresh after final runs)

| scenario | n | time-to-comment p50 | p95 | wait for runner p50 | runner | LLM p50 / p95 |
|---|---|---|---|---|---|---|
| cold baseline (KEDA ScaledJob, no pool, no LLM) | 1 | ~35s | — | ~15s (pod schedule) | 6s (+16s pod) | — |
| quiet, warm pool (single PRs) | 20 | 24s | 72s | 0.5s | 3.9s | 14.9s / 25.1s |
| burst (56 PRs, cap 4, pool 4) | 121 | 98s | 224s | 33s | 4.0s | 14.2s / 38.8s |
| burst, fixed pool, `full` only (Oct 7, from Postgres) | 56 | 70s | 200s | 39s | 4.0s | 16.0s / 28.7s |
| burst, autoscaled (KEDA 4→16 + Karpenter spot node, 32 workers; Oct 10) | 56 | 165s | 242s | 40s | 4.1s | 9.3s / 18.0s |
| burst, autoscaled + App posting (per-installation buckets, one gap per task, no self-review retry; Oct 10) | 56 | 81s | 143s | 40s | 4.0s | 8.1s / 16.2s |

Eval run 1 (gpt-6-astra @ high, judge gpt-6-luna): full = 35/35 strict, 34/35 semantic, FP 0/20; diff_only = 34/35 strict, 34/35 semantic, FP 0/20. One equivalent mutant excluded. Noisy v2 (bug inside a real 2-6 file upstream diff, n=11): full 10/11, diff_only 11/11 strict (10/11 semantic). Configs indistinguishable at this n.
Guided (full + 44 mined guidelines): 45/46, FP 0/20, +3.6k tokens/PR, identical to full. Combined over all 46 bugs: full 97.8%, diff_only 97.8%, guided 97.8%; FP 0/20 for all three.
Cost: ~$0.04–0.06 tokens per average review, $0.21 for a 19k-token one; compute $0.00004; idle pool of 4 = $0.16/hr.
Agentic (grep/read_file/list_dir, 15 calls): 45/46 strict, 43/46 semantic, 24k tokens/PR ($0.26) vs 11k ($0.11) for full, p50 145s vs 58s. Exploration = pure cost on this corpus.
Four-way: diff_only ≈ full ≈ guided ≈ agentic on recall (97.8% strict); they differ only in cost/latency.
Karpenter: pool 4→16, node Ready in 41s, 16 runners in 49s; spot c7g.2xlarge after creating the Spot service-linked role. Drain mid-task: lost pod detected, task requeued, posted on attempt 2 (50s), 0 failed.
Multi-repo (pallets/click, never seen): clone 0.4s, install 3.0s, 2,238 tests 14.7s, 18.7s total vs ~4s on the warm Flask seed.
GitHub App: reviews as pr-runtime[bot], Check run per task (blocks on blockers, never approves), @pr-runtime mention trigger.
Tokens: ~4k in / 0.4k out per review. 112 reviews ≈ 0.45M input tokens.
Autoscaled burst (Oct 10): HPA asked for 16 at t+10s, c8g.2xlarge spot node Ready at t+42s, 16 runners at t+78s, runner stage drained at t+86s (vs ~105s fixed); pool back to 4 by t+295s, node consolidated ~t+360s; 0 failed, 0 requeued; burst node ≈ $0.016, tokens $6.04. Time-to-comment got worse (p50 165s vs 70s) because GitHub posting is serialized and became the queue once runners and the LLM stopped metering reviews out: `post` p50 100s. The two writes per task turned out to be the PAT self-review 422 + retry, not the Check.
Posting burst (Oct 10, third run): same 56 PRs posted as the App with a Check each; writes bucketed per installation, one 1.5s gap per task. Time-to-comment p50 81s / p95 143s, post wait p50 23s / p95 61s, last review at t+151s (vs t+253s); 168 writes, none rate-limited; 0 failed. Honest reading: one installation means the buckets could not help here; most of the gain is "no self-review retry as the App" (one write per review instead of two). First attempt deadlocked (GitHub slot held across a Postgres row lock) and was fixed before the measurement. Private repos now get an API-diff fallback review labelled "Tests not run".

Talking points from these: under burst, the runner is never the bottleneck (4s); queue wait (cap) and the LLM stage are, and once those are opened up the serialized egress is. Autoscaling moved the queue, it did not shorten it. Pool N > cap M removes the refill wait; a separate LLM concurrency knob removes the review backlog. Cold vs warm matters most at the *first* wave; after that it is a throughput question.
