# Eval noisy-20261007-222346

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |
|---|---|---|---|---|---|---|---|---|
| diff_only | 11 | 1.0 | 1.0 | 0 | None | None | 41.9s | 59.0s |
| full | 11 | 1.0 | 1.0 | 0 | None | None | 47.6s | 62.2s |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| diff_only | noisy/synthetic | 11 | 1.0 | 1.0 | 1.0 |
| diff_only | noisy | 11 | 1.0 | 1.0 | 1.0 |
| full | noisy/synthetic | 11 | 1.0 | 1.0 | 1.0 |
| full | noisy | 11 | 1.0 | 1.0 | 1.0 |
