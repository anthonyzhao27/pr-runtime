# Eval agentic-20261008-010344

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total | tokens in/out | $/PR tokens | $/PR compute |
|---|---|---|---|---|---|---|---|---|---|---|---|
| agentic | 46 | 0.978 | 0.935 | 20 | 0.0 | 0 | 144.5s | 335.3s | 24297/395 | 0.2627 | 4.4e-05 |
| full | 46 | 0.978 | 0.978 | 20 | 0.0 | 0 | 57.7s | 190.8s | 11479/398 | 0.003 | 1e-06 |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| agentic | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| agentic | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| agentic | red | 10 | 1.0 | 1.0 | 1.0 |
| agentic | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| agentic | green/synthetic | 21 | 1.0 | 0.905 | 1.0 |
| agentic | green | 25 | 1.0 | 0.92 | 1.0 |
| agentic | noisy/synthetic | 11 | 0.909 | 0.909 | 0.909 |
| agentic | noisy | 11 | 0.909 | 0.909 | 0.909 |
| full | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| full | red | 10 | 1.0 | 1.0 | 1.0 |
| full | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | green/synthetic | 21 | 1.0 | 1.0 | 1.0 |
| full | green | 25 | 1.0 | 1.0 | 1.0 |
| full | noisy/synthetic | 11 | 0.909 | 0.909 | 0.909 |
| full | noisy | 11 | 0.909 | 0.909 | 0.909 |
