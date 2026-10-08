# Decisions log

Append-only. One entry per decision or surprise, newest at the bottom. Each entry: context, what we did, what we rejected, what it cost, what we'd say if asked.

## 2026-10-07 — Graviton nodes instead of x86
Laptop is arm64 (Docker 28, aarch64). Cross-building for amd64 adds minutes per image and a class of "works locally, fails in pod" bugs. Chose `m7g.large` managed node group so images build natively. Cost: ARM wheels for a few dev deps could be missing; Flask's dev extras are pure Python, so low risk.

## 2026-10-07 — EC2 on-demand vCPU quota is 5
`L-1216C47A` = 5 vCPU in us-east-1 on a fresh-ish account. Three `m7g.large` is 6. Requested increase to 32 (`9c866f461a7d420e8c3cc67bea590b15sI75Z0QF`, PENDING). Approved within hours to **64** (more than asked). Spot quota is also 5, so spot would not have helped at the time. Lesson for the talk: capacity is a quota problem before it is a scheduler problem.

## 2026-10-07 — Root credentials in the `personal` profile
`aws sts get-caller-identity` returns the account root. Works, but should be an IAM user with admin + MFA. Flagged, not blocking.
