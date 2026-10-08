# Eval latest

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |
|---|---|---|---|---|---|---|---|---|
| diff_only | 36 | 0.944 | 0.917 | 20 | 0.0 | 0 | 116.7s | 238.1s |
| full | 36 | 0.972 | 0.972 | 20 | 0.0 | 0 | 67.5s | 200.3s |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| diff_only | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| diff_only | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| diff_only | red | 10 | 1.0 | 1.0 | 1.0 |
| diff_only | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| diff_only | green/synthetic | 22 | 0.909 | 0.864 | 0.909 |
| diff_only | green | 26 | 0.923 | 0.885 | 0.923 |
| full | red/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | red/synthetic | 6 | 1.0 | 1.0 | 1.0 |
| full | red | 10 | 1.0 | 1.0 | 1.0 |
| full | green/historical | 4 | 1.0 | 1.0 | 1.0 |
| full | green/synthetic | 22 | 0.955 | 0.955 | 0.955 |
| full | green | 26 | 0.962 | 0.962 | 0.962 |
