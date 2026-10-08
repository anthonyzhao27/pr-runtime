# Eval combined: 20261007-221030+noisy-20261007-233347+guided-20261008-001654

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | p50 total (burst) | p95 | tokens in/out |
|---|---|---|---|---|---|---|---|---|
| diff_only | 46 | 0.978 | 0.956 | 20 | 0.0 | 116.7s | 238.1s | -/- |
| full | 46 | 0.978 | 0.956 | 20 | 0.0 | 67.5s | 200.3s | -/- |
| guided | 46 | 0.978 | 0.978 | 20 | 0.0 | 144.3s | 265.4s | 15098/463 |

| config | variant | n | strict | semantic |
|---|---|---|---|---|
| diff_only | red/historical | 4 | 1.0 | 1.0 |
| diff_only | red/synthetic | 6 | 1.0 | 1.0 |
| diff_only | green/historical | 4 | 1.0 | 1.0 |
| diff_only | green/synthetic | 21 | 0.952 | 0.952 |
| diff_only | noisy/synthetic | 11 | 1.0 | 0.909 |
| full | red/historical | 4 | 1.0 | 1.0 |
| full | red/synthetic | 6 | 1.0 | 1.0 |
| full | green/historical | 4 | 1.0 | 1.0 |
| full | green/synthetic | 21 | 1.0 | 0.952 |
| full | noisy/synthetic | 11 | 0.909 | 0.909 |
| guided | red/historical | 4 | 1.0 | 1.0 |
| guided | red/synthetic | 6 | 1.0 | 1.0 |
| guided | green/historical | 4 | 1.0 | 1.0 |
| guided | green/synthetic | 21 | 1.0 | 1.0 |
| guided | noisy/synthetic | 11 | 0.909 | 0.909 |
