# Burst 2026-10-10 — autoscaled pool (KEDA 4..16 + Karpenter) vs fixed pool

Same 56 PRs as `20261007-221030` (config `full`), re-fired via `POST /api/tasks/{id}/rerun` in 3.4s by `scripts/burst_rerun.py` on the driver box. Raw: `~/burst-autoscale2/{samples.jsonl,tasks.json}` on the driver (not committed). Analysis in `docs/DECISIONS.md` (2026-10-10, "Closing the autoscaling loop").

| metric | fixed (Oct 7) | autoscaled (Oct 10) |
|---|---|---|
| time-to-comment p50 / p95 | 70.1s / 200.3s | 164.8s / 242.2s |
| wait for runner p50 / p95 | 38.9s / 73.2s | 39.8s / 61.9s |
| runner p50 | 4.0s | 4.1s |
| LLM p50 / p95 | 16.0s / 28.7s | 9.3s / 18.0s |
| GitHub post p50 / p95 | 1.6s / 1.8s | 100.2s / 131.4s |
| runner stage drained | ~105s | 86s |
| pool max | 4 | 16 (desired t+10s, Ready t+78s) |
| burst node | none | c8g.2xlarge spot: claimed t+10s, Ready t+42s, consolidated ~t+360s |
| failed / retried | 0 / 0 | 0 / 0 |
| tokens | $5.39 | $6.04 (489k in / 23k out) |
| burst compute | — | ~$0.016 |

## Timeline (driver script output, 5s samples)

```
fired 56 reruns in 3.4s
t+    9s done 0/56 pending=24.0 busy=0.0 ready=4.0 cap=4.0 inreview=4.0 hpa=4/4 pods=4+0p claims=0
t+   15s done 1/56 pending=52.0 busy=0.0 ready=2.0 cap=2.0 inreview=4.0 hpa=16/4 pods=7+9p claims=1
t+   21s done 2/56 pending=52.0 busy=0.0 ready=2.0 cap=2.0 inreview=4.0 hpa=16/4 pods=7+9p claims=1
t+   26s done 3/56 pending=45.0 busy=2.0 ready=3.0 cap=3.0 inreview=8.0 hpa=16/4 pods=7+9p claims=1
t+   32s done 4/56 pending=41.0 busy=1.0 ready=4.0 cap=4.0 inreview=11.0 hpa=16/16 pods=6+10p claims=1
t+   37s done 5/56 pending=34.0 busy=4.0 ready=1.0 cap=1.0 inreview=14.0 hpa=16/16 pods=4+12p claims=1
t+   43s done 6/56 pending=34.0 busy=4.0 ready=1.0 cap=1.0 inreview=14.0 hpa=16/16 pods=4+12p claims=1
t+   48s done 6/56 pending=28.0 busy=5.0 ready=2.0 cap=2.0 inreview=17.0 hpa=16/16 pods=16+0p claims=1
t+   54s done 7/56 pending=15.0 busy=5.0 ready=2.0 cap=2.0 inreview=30.0 hpa=16/16 pods=5+11p claims=1
t+   59s done 8/56 pending=15.0 busy=5.0 ready=2.0 cap=2.0 inreview=30.0 hpa=16/16 pods=5+11p claims=1
t+   65s done 9/56 pending=11.0 busy=3.0 ready=3.0 cap=3.0 inreview=34.0 hpa=16/16 pods=5+11p claims=1
t+   70s done 9/56 pending=4.0 busy=3.0 ready=5.0 cap=5.0 inreview=40.0 hpa=16/16 pods=15+1p claims=1
t+   75s done 11/56 pending=4.0 busy=3.0 ready=5.0 cap=5.0 inreview=40.0 hpa=16/16 pods=15+1p claims=1
t+   81s done 12/56 pending=0.0 busy=1.0 ready=8.0 cap=8.0 inreview=45.0 hpa=16/16 pods=10+6p claims=1
t+   86s done 15/56 pending=0.0 busy=0.0 ready=11.0 cap=11.0 inreview=43.0 hpa=16/16 pods=16+0p claims=1
t+   91s done 15/56 pending=0.0 busy=0.0 ready=11.0 cap=11.0 inreview=43.0 hpa=16/16 pods=16+0p claims=1
t+   96s done 16/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=41.0 hpa=16/16 pods=16+0p claims=1
t+  102s done 16/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=40.0 hpa=16/16 pods=16+0p claims=1
t+  107s done 17/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=40.0 hpa=16/16 pods=16+0p claims=1
t+  112s done 17/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=39.0 hpa=16/16 pods=16+0p claims=1
t+  117s done 17/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=39.0 hpa=16/16 pods=16+0p claims=1
t+  123s done 18/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=39.0 hpa=16/16 pods=16+0p claims=1
t+  128s done 18/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=39.0 hpa=16/16 pods=16+0p claims=1
t+  133s done 20/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=37.0 hpa=16/16 pods=16+0p claims=1
t+  138s done 21/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=37.0 hpa=16/16 pods=16+0p claims=1
t+  144s done 22/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=35.0 hpa=16/16 pods=16+0p claims=1
t+  149s done 23/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=33.0 hpa=16/16 pods=16+0p claims=1
t+  154s done 24/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=33.0 hpa=16/16 pods=16+0p claims=1
t+  159s done 25/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=32.0 hpa=16/16 pods=16+0p claims=1
t+  165s done 27/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=32.0 hpa=16/16 pods=16+0p claims=1
t+  170s done 29/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=32.0 hpa=16/16 pods=16+0p claims=1
t+  175s done 31/56 pending=0.0 busy=0.0 ready=16.0 cap=16.0 inreview=32.0 hpa=16/16 pods=16+0p claims=1
t+  181s done 32/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/16 pods=12+0p claims=1
t+  186s done 34/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/16 pods=12+0p claims=1
t+  191s done 36/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/16 pods=12+0p claims=1
t+  196s done 38/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  202s done 40/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  207s done 41/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  212s done 42/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  217s done 44/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  223s done 45/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  228s done 47/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  233s done 48/56 pending=0.0 busy=0.0 ready=12.0 cap=12.0 inreview=32.0 hpa=12/12 pods=12+0p claims=1
t+  238s done 50/56 pending=0.0 busy=0.0 ready=8.0 cap=8.0 inreview=32.0 hpa=8/12 pods=12+0p claims=1
t+  244s done 52/56 pending=0.0 busy=0.0 ready=8.0 cap=8.0 inreview=32.0 hpa=8/12 pods=8+0p claims=1
t+  249s done 54/56 pending=0.0 busy=0.0 ready=8.0 cap=8.0 inreview=32.0 hpa=8/12 pods=8+0p claims=1
t+  254s done 56/56 pending=0.0 busy=0.0 ready=8.0 cap=8.0 inreview=32.0 hpa=8/8 pods=8+0p claims=1
== burst summary ==
n=56 posted=56 failed=0 attempts>1=0 cold=44
fired in 3.4s; last posted at t+253s; drained in 254s; settled at t+365s
hpa max current=16 at t+26s; first desired>4 at t+10s (desired=16)
max running runner pods=16; max on burst nodes=9; max ready (controller)=16.0; max cap=16.0
nodeclaim runner-burst-brg2s: c8g.2xlarge spot first_seen t+10s ready t+42s last_seen t+352s
final: hpa=4/4 pods=4 claims=0
```

## Scale-down (laptop watch, 10s samples)

```
21:44:20 hpa=4/4 0 pods=   4 Running  claims=
21:44:32 hpa=16/4 5500m pods=   9 Pending    7 Running  claims=runner-burst-brg2s c8g.2xlarge spot Unknown 5s;
21:44:44 hpa=16/16 2813m pods=   1 ContainerCreating    9 Pending    6 Running  claims=runner-burst-brg2s c8g.2xlarge spot Unknown 17s;
21:44:56 hpa=16/16 2813m pods=   1 ContainerCreating    9 Pending    5 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal Unknown;
21:45:08 hpa=16/16 2125m pods=   2 ContainerCreating    3 Pending   11 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:45:19 hpa=16/16 1188m pods=  10 ContainerCreating    6 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:45:31 hpa=16/16 438m pods=   2 ContainerCreating   14 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:45:43 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:45:55 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:46:07 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:46:19 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:46:31 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:46:44 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:46:56 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:47:08 hpa=16/16 0 pods=  16 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:47:20 hpa=12/16 0 pods=  12 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:47:32 hpa=12/12 0 pods=  12 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:47:45 hpa=12/12 0 pods=  12 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:47:57 hpa=12/12 0 pods=  12 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:48:08 hpa=12/12 0 pods=  12 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:48:20 hpa=8/12 0 pods=   8 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:48:32 hpa=8/8 0 pods=   8 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:48:44 hpa=8/8 0 pods=   8 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:48:56 hpa=8/8 0 pods=   8 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:49:08 hpa=8/8 0 pods=   8 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:49:19 hpa=4/8 0 pods=   4 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:49:31 hpa=4/4 0 pods=   4 Running  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
21:49:43 hpa=4/4 0 pods=   4 Running    1 Terminating  claims=runner-burst-brg2s c8g.2xlarge spot ip-10-0-25-205.ec2.internal True;
```
