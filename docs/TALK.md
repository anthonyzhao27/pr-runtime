# Talk: pr-runtime (Uncountable onsite, Oct 15, internship)

**Slot: 30 minutes total, including Q&A.** Plan for **12 minutes of talk, 4 of video inside it, ~15 of questions.** If they interrupt early, let them; the video and the numbers slide are the two things that must happen.

The audience is the engineers who built Circus. They are not testing whether you can out-build them. They are testing: can you explain what you made, do you understand your own decisions, did you measure honestly, and what would you do next.

---

## The 60-second version (memorize this)

> I built a GitHub App that reviews pull requests. When a PR opens, or someone tags `@pr-runtime`, the event goes through API Gateway and a Lambda that checks the signature into a queue. A controller inside an EKS cluster pulls the job and hands it to a warm, throwaway pod that has no secrets: it clones the repo, runs the tests and linter, and reports back. The controller sends the diff and the test evidence to a model and posts inline review comments and a Check run as the bot. Median 24 seconds from PR open to comment.
>
> The interesting part was measuring it. I planted 46 real and synthetic bugs in a Flask fork plus 20 clean PRs: the reviewer caught 97.8% with no false positives, and four different amounts of context all tied. Under a burst of 56 PRs, the pods were never the bottleneck; the LLM stage was, and after I autoscaled the pool, GitHub's rate limit on posting was. Your post describes this exact shape, Circus, so I built the reviewer half of it to see what it costs and what it buys.

---

## Slides (12 min)

| # | Slide | Time | Say |
|---|---|---|---|
| 1 | Title + the question | 0:30 | "What does it cost to run untrusted PR code per task on Kubernetes, and what does a warm pool buy?" |
| 2 | Your post, my shape | 1:00 | Quote the stack row and "every task in its own ephemeral pod, warm pool." "I built the Rover half: review, not fixing." |
| 3 | System overview image | 2:00 | Walk the 7 numbered steps. Point at the trust split: runner has nothing, controller has everything. |
| 4 | **Demo video** | 4:00 | Narrate over it (script below). |
| 5 | Numbers | 2:00 | One table (below). Say the three honest findings out loud. |
| 6 | Eval | 1:30 | 46 bugs, 20 clean, 97.8%, 0 FP, four configs tie. "Context beyond the diff didn't help on this corpus; the harness was wrong before the model was, twice." |
| 7 | What I'd do next | 1:00 | Per-installation posting (measured), fallback review for private repos (done), gVisor, a harder corpus. Stop. |

Keep slides sparse: the image, the table, one chart. Everything else is you talking.

---

## Demo video script (4 min, pre-recorded Oct 12, narrated live)

| Time | On screen | Say |
|---|---|---|
| 0:00 | Terminal: `kubectl get pods -n pr-runtime -w`, four idle runners | "Four warm pods waiting. Each is a disposable test box with no secrets." |
| 0:20 | Open a PR with a hidden bug (secret-key rotation) | "This PR reverts a real Flask bug fix. Tests stay green." |
| 0:35 | Controller logs: queued, assigned (warm, <1s), result, LLM, posted | "A runner takes it, runs the suite in 4 seconds, the model reviews." |
| 1:00 | GitHub: inline comment from `pr-runtime[bot]`, REQUEST_CHANGES, Check run | "Found it, on the line, with a failing Check. CI was green." |
| 1:20 | Comment `@pr-runtime review` on the click fork PR | "Same thing by mention, on a repo it had never seen. It clones and detects the toolchain." |
| 1:50 | Console: task detail, phase bar, cost, thumbs-up | "Where the time and money went per task." |
| 2:10 | `scripts/burst_rerun.py 56` | "Now 56 at once." |
| 2:20 | Grafana: pending spikes, pool 4→16, spot node appears, in-review climbs | "KEDA scales the pool, Karpenter adds a spot node in 40 seconds, and the next bottleneck shows up." |
| 3:20 | Numbers slide | "Here's what that bought." |

Backup: if the video file fails, slides 3 and 5 carry the talk. Have screenshots of the PR and the Grafana burst in the deck.

---

## Numbers (the one table)

| scenario | time to comment p50 | p95 | note |
|---|---|---|---|
| cold start (KEDA Job per PR, no pool) | ~35s | | the number the pool beats |
| quiet, warm pool, seed repo | 24s | 72s | runner 4s, LLM 15s, post 2s |
| quiet, repo never seen (click, 2,238 tests) | 29s | | clone 0.3s, install 4s, tests 13s |
| 56-PR burst, fixed pool 4 | 70s | 200s | queue wait and LLM stage dominate |
| 56-PR burst, autoscaled 4→16 + spot node | 165s | 242s | runners faster; serialized GitHub posting became the bottleneck |
| 56-PR burst, autoscaled + posting as the App | 81s | 143s | one write per review instead of self-review 422 + retry; post wait p50 23s; last review at t+151s |

Eval: 46 bugs (8 historical, 38 synthetic incl. 11 hidden in real multi-file diffs), 20 clean PRs. Strict recall 97.8%, judge-semantic 95.6%, FP 0/20, all four configs. Cost: $0.04–0.26 per review in tokens, ~$0.00004 compute, idle pool $0.16/hr.

**The three honest findings, said plainly:**
1. The runners were never the bottleneck. Scaling the cheap stage exposed the next one twice.
2. More context didn't find more bugs on this corpus. The diff was enough. The configs differ only in cost.
3. The eval harness was wrong before the model was: equivalent mutants, and a "noisy" variant that wasn't noisy because PR diffs come from the merge-base.
4. My first diagnosis of the posting bottleneck was wrong too: the extra write per task was the PAT-era self-review retry, not the Check. Posting as the App removed it; per-installation buckets can't help a single-tenant burst. Say this one if they ask what you got wrong.

---

## Likely questions (internship level) and short answers

**About the system**
1. *Walk me through what happens when a PR opens.* → The 60-second version, steps 1–7.
2. *Why a queue instead of a webhook straight to your service?* → Nothing in the cluster is public. The controller pulls; the Lambda already rejected anything unsigned. The queue also absorbs bursts.
3. *Why does the runner have no secrets?* → It runs the PR's code. `pytest` imports it; a `conftest.py` can do anything. So the pod can only reach the controller, DNS, and port 443. Verified by trying from inside.
4. *Why a pod per task and why delete it?* → Fresh workdir every time, nothing leaks between PRs. Deleting is the boundary; a container restart would reuse the pod.
5. *Why Kubernetes at all for API calls?* → For a pure LLM reviewer you wouldn't. I ran untrusted code per task, which needs isolation, a warm pool, and burst, and that's your platform's shape. Lambda per task would be the other reasonable choice.
6. *What's KEDA vs Karpenter?* → KEDA sets how many runner pods from my backlog metric; Karpenter adds nodes when those pods don't fit. My controller's admission cap follows the Ready count.
7. *What happens if a runner dies mid-task?* → The controller sees the pod vanish and requeues immediately. Tested by draining a spot node: one retry, nothing failed.
8. *What happens if your controller dies?* → State is in Postgres. On boot it re-enqueues running and queued tasks and resumes reviews. Tested: 17 reviews resumed after a restart.
9. *Can the bot approve a PR?* → Technically yes, it shouldn't. It posts findings and a Check that can block; approval stays with humans or a ruleset.
10. *How does it work for other repos?* → Install the App. The runner clones on demand and detects the toolchain; Python today, others get a diff-only review. Private repos get an API-diff review because the runner can't clone them.

**About the measurement**
11. *How do you know it's good?* → The eval. Planted bugs with known lines, strict and judge scoring, clean PRs for false positives.
12. *What surprised you?* → The three findings above. Pick the posting bottleneck; it's the freshest.
13. *What would you change?* → A late Check as one completed write (the check opener still trails under burst), stronger isolation (gVisor), a harder corpus with cross-file bugs, private-repo cloning with a scoped token instead of the API-diff fallback.

**About you**
14. *How much of this did you write?* → Honest answer: I designed it, drove it, and made the calls; a coding agent wrote most of the code, and I own every decision in the DECISIONS log. Say this once, confidently, before they ask.
15. *What was hardest?* → Not the cluster. Getting honest numbers: every time I fixed the harness the answer changed.

---

## Deep questions (appendix: skim, don't rehearse)

- gVisor/Firecracker vs containers; what NetworkPolicy can't stop (any host on 443).
- Why SQS over NATS; why diff-size ranking and starvation; cap vs pool size.
- Spot: why it's fine for 4-second replayable tasks and wrong for the control plane.
- Pod Identity vs IRSA; EKS access entries; why the driver box needed a security-group rule.
- Merge-base diffs and why the first noisy corpus wasn't noisy.
- Why Terraform rolled every node once (module tracks latest AMI; now pinned).
- Secondary rate limits; per-installation tokens; check-run ids overflow int32.
- SQLAlchemy pool starved by 32 review workers.

If any of these come up, answer from `docs/DECISIONS.md`; each has an entry.

---

## Day-of checklist

- Video file on the laptop and on a USB stick. Screenshots in the deck as backup.
- Slides: overview image, numbers table, eval table, next-steps list. Nothing else.
- Cluster does not need to be up. If it is, `kubectl port-forward` for the console as a bonus.
- Say the "how much did you write" line yourself, early.
- When you don't know: "I didn't measure that; here's how I would."

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
