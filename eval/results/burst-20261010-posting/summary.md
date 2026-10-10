# Burst 2026-10-10 (second) — per-installation posting, one gap per task, reviews as the App

Same 56 PRs as `20261007-221030` and `burst-20261010-autoscale` (config `full`), re-fired via `POST /api/tasks/{id}/rerun` in 3.8s by `scripts/burst_rerun.py` on the driver box. First attempt of this burst deadlocked the controller (GitHub slot held across a row-lock wait; see `docs/DECISIONS.md` 2026-10-10 "Posting: per installation, one gap per task") and was abandoned; this is the clean rerun after the fix. Raw: `~/burst-posting/{samples.jsonl,tasks.json,run.log}` on the driver (not committed). Reviews posted as `pr-runtime[bot]` with a Check run per task; the two earlier bursts posted with the PAT and no Check.

| metric | fixed (Oct 7) | autoscaled (Oct 10, PAT) | posting (Oct 10, App) |
|---|---|---|---|
| time-to-comment p50 / p95 | 70.1s / 200.3s | 164.8s / 242.2s | **81.4s / 142.9s** |
| wait for runner p50 / p95 | 38.9s / 73.2s | 39.8s / 61.9s | 40.4s / 68.0s |
| runner p50 | 4.0s | 4.1s | 4.0s |
| LLM p50 / p95 | 16.0s / 28.7s | 9.3s / 18.0s | 8.1s / 16.2s |
| GitHub post (slot wait + write) p50 / p95 | 1.6s / 1.8s | 100.2s / 131.4s | 23.3s / 61.1s ¹ |
| GitHub writes | 112 (56 reviews, each a 422 + retry) | 112 (same) | 168 (56 reviews + 56 check opens + 56 check finishes) |
| runner stage drained | ~105s | 86s | 85s |
| last review posted | t+258s | t+253s | t+151s |
| last GitHub write | — | — | t+330s (trailing check opens/finishes) |
| pool max | 4 | 16 (desired t+10s, Ready t+78s) | 16 (desired t+10s, 16 Running t+95s) |
| burst node | none | c8g.2xlarge spot: claimed t+10s, Ready t+42s | m8g.2xlarge spot: claimed t+10s, Ready t+42s, gone t+~350s |
| failed / retried | 0 / 0 | 0 / 0 | 0 / 0 |
| tokens | $5.39 | $6.04 (489k in / 23k out) | $6.03 (487k in / 23k out) |
| burst compute | — | ~$0.016 | ~$0.016 + $0.003 runner-seconds |

¹ The script's own `post` line printed p50 0.8s because this build's timer started inside the slot (fixed in the next commit); the 23.3s / 61.1s above is `posted_at - result_at - llm` from Postgres, i.e. the wait for the slot plus the write, the same quantity the two earlier columns measured.

## Where the writes went (controller log, 30s bins from the first task creation)

```
assign  n= 56 first t+  1.4 last t+ 71.8   19 26 11  0  0  0  0  0  0  0  0
llm     n= 70 first t+  9.8 last t+ 86.8   14 24 32  0  0  0  0  0  0  0  0
review  n= 56 first t+ 12.6 last t+150.9    6 13 12 12 12  1  0  0  0  0  0
open    n= 56 first t+  0.7 last t+327.7    8  1  1  0  0  8  7  8  8  7  8
finish  n= 56 first t+ 13.0 last t+329.5    2  0  2  1  4  8  8  7  8  8  8
```
Review posts ran at one per ~2.5s (1.5s gap + ~1s POST) from t+12s to t+151s; the LLM stage was done at t+87s, so the last ~60s is pure posting backlog (`inreview` pinned at 31 = workers queued on the slot). The single check-opener thread lost the (unfair) lock to the 32 review threads: 9 tasks got review + finish in one slot, 47 had their Check opened after the review and finished in the next slot, trailing to t+330s. 167 writes in 327s, gap p50 1.99s, no 403/429.

## Timeline (driver script output, every other 5s sample)

```
t+    9s done 0/56 pending=23.0 busy=0.0 ready=4.0 cap=4.0 inreview=1.0 hpa=4/4 pods=4+0p claims=0
t+   20s done 2/56 pending=52.0 busy=0.0 ready=2.0 cap=2.0 inreview=4.0 hpa=16/4 pods=7+9p claims=1
t+   31s done 6/56 pending=40.0 busy=3.0 ready=2.0 cap=2.0 inreview=9.0 hpa=16/16 pods=5+11p claims=1
t+   41s done 11/56 pending=36.0 busy=2.0 ready=3.0 cap=3.0 inreview=12.0 hpa=16/16 pods=5+11p claims=1
t+   53s done 15/56 pending=19.0 busy=6.0 ready=7.0 cap=7.0 inreview=17.0 hpa=16/16 pods=7+9p claims=1
t+   63s done 20/56 pending=12.0 busy=2.0 ready=0.0 cap=0.0 inreview=25.0 hpa=16/16 pods=6+10p claims=1
t+   74s done 24/56 pending=9.0 busy=1.0 ready=4.0 cap=4.0 inreview=26.0 hpa=16/16 pods=10+6p claims=1
t+   85s done 28/56 pending=0.0 busy=0.0 ready=11.0 cap=11.0 inreview=31.0 hpa=16/16 pods=11+5p claims=1
t+   95s done 33/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
t+  106s done 36/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
t+  116s done 41/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
t+  127s done 45/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
t+  137s done 50/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
t+  148s done 54/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=31.0 hpa=16/16 pods=16+0p claims=1
== burst summary ==
fired 56 reruns in 3.8s
== burst summary ==
n=56 posted=56 failed=0 attempts>1=0 cold=37
fired in 3.8s; last posted at t+151s; drained in 153s; settled at t+364s
total         n= 56 p50=   81.4 p95=  142.9 max=  150.3
wait          n= 56 p50=   40.4 p95=   68.0 max=   71.2
runner_total  n= 56 p50=    4.0 p95=    6.0 max=    6.6
llm           n= 56 p50=    8.1 p95=   16.2 max=   27.1
post          n= 56 p50=    0.8 p95=    1.0 max=    1.1
tokens in=486582 out=23176 cost tokens=$6.025 compute=$0.00267
hpa max current=16 at t+26s; first desired>4 at t+10s (desired=16)
max running runner pods=16; max on burst nodes=9; max ready (controller)=16.0; max cap=16.0
nodeclaim runner-burst-brrgq: m8g.2xlarge spot first_seen t+10s ready t+42s last_seen t+344s
final: hpa=4/4 pods=4 claims=0
	
```
