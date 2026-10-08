# Eval noisy-20261007-233347

| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |
|---|---|---|---|---|---|---|---|---|
| diff_only | 11 | 1.0 | 0.909 | 0 | None | None | 44.3s | 53.6s |
| full | 11 | 0.909 | 0.909 | 0 | None | None | 46.6s | 60.1s |

## By variant

| config | variant | n | recall (strict) | recall (semantic) | flagged |
|---|---|---|---|---|---|
| diff_only | noisy/synthetic | 11 | 1.0 | 0.909 | 1.0 |
| diff_only | noisy | 11 | 1.0 | 0.909 | 1.0 |
| full | noisy/synthetic | 11 | 0.909 | 0.909 | 0.909 |
| full | noisy | 11 | 0.909 | 0.909 | 0.909 |
