# Eval 20261007-221030

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |
|---|---|---|---|---|---|---|---|---|
| diff_only | 35 | 0.971 | 0.971 | 20 | 0.0 | 0 | 116.7s | 238.1s |
| full | 35 | 1.0 | 0.971 | 20 | 0.0 | 0 | 67.5s | 200.3s |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| diff_only | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| diff_only | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| diff_only | red | 10 | 1.0 | 1.0 | 1.0 |
| diff_only | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| diff_only | green/synthetic | 21 | 0.952 | 0.952 | 0.952 |
| diff_only | green | 25 | 0.96 | 0.96 | 0.96 |
| full | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| full | red | 10 | 1.0 | 1.0 | 1.0 |
| full | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | green/synthetic | 21 | 1.0 | 0.952 | 1.0 |
| full | green | 25 | 1.0 | 0.96 | 1.0 |
