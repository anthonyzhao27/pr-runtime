# Eval guided-20261008-001654

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total | tokens in/out | $/PR tokens | $/PR compute |
|---|---|---|---|---|---|---|---|---|---|---|---|
| full | 46 | 0.978 | 0.978 | 20 | 0.0 | 0 | 57.7s | 190.8s | 11479/398 | 0 | 0 |
| guided | 46 | 0.978 | 0.978 | 20 | 0.0 | 0 | 144.3s | 265.4s | 15098/463 | 0.1741 | 4.5e-05 |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| full | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| full | red | 10 | 1.0 | 1.0 | 1.0 |
| full | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | green/synthetic | 21 | 1.0 | 1.0 | 1.0 |
| full | green | 25 | 1.0 | 1.0 | 1.0 |
| full | noisy/synthetic | 11 | 0.909 | 0.909 | 0.909 |
| full | noisy | 11 | 0.909 | 0.909 | 0.909 |
| guided | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| guided | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| guided | red | 10 | 1.0 | 1.0 | 1.0 |
| guided | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| guided | green/synthetic | 21 | 1.0 | 1.0 | 1.0 |
| guided | green | 25 | 1.0 | 1.0 | 1.0 |
| guided | noisy/synthetic | 11 | 0.909 | 0.909 | 1.0 |
| guided | noisy | 11 | 0.909 | 0.909 | 1.0 |
