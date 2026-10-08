# Eval noisy-20261007-222346

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |
|---|---|---|---|---|---|---|---|---|
| diff_only | 13 | 0.846 | 0.769 | 0 | None | None | 41.9s | 59.0s |
| full | 13 | 0.846 | 0.846 | 0 | None | None | 47.6s | 62.2s |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| diff_only | noisy/synthetic | 13 | 0.846 | 0.769 | 0.846 |
| diff_only | noisy | 13 | 0.846 | 0.769 | 0.846 |
| full | noisy/synthetic | 13 | 0.846 | 0.846 | 0.846 |
| full | noisy | 13 | 0.846 | 0.846 | 0.846 |
