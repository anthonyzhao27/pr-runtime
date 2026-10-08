# Decisions log

Append-only. One entry per decision or surprise, newest at the bottom. Each entry: context, what we did, what we rejected, what it cost, what we'd say if asked.

## 2026-10-07 — Graviton nodes instead of x86
Laptop is arm64 (Docker 28, aarch64). Cross-building for amd64 adds minutes per image and a class of "works locally, fails in pod" bugs. Chose `m7g.large` managed node group so images build natively. Cost: ARM wheels for a few dev deps could be missing; Flask's dev extras are pure Python, so low risk.

## 2026-10-07 — EC2 on-demand vCPU quota is 5
`L-1216C47A` = 5 vCPU in us-east-1 on a fresh-ish account. Three `m7g.large` is 6. Requested increase to 32 (`9c866f461a7d420e8c3cc67bea590b15sI75Z0QF`, PENDING). Approved within hours to **64** (more than asked). Spot quota is also 5, so spot would not have helped at the time. Lesson for the talk: capacity is a quota problem before it is a scheduler problem.

## 2026-10-07 — Root credentials in the `personal` profile
`aws sts get-caller-identity` returns the account root. Works, but should be an IAM user with admin + MFA. Flagged, not blocking.

## 2026-10-07 — Flask's test suite takes 1.1 seconds, not 30
494 tests, 1.14s locally; `uv sync` of test deps ~1.5s warm, ruff 0.3s. The "expensive" part of a task is not the tests. It is image pull + clone + dependency install, i.e. everything the warm pool pre-pays. Good for the thesis, bad for a dramatic demo: pytest will not be the visible wait. Expect the LLM call to dominate the warm path.

## 2026-10-07 — Read-only root filesystem vs a baked-in clone
The clone is baked into the image at `/opt/seed/flask`. With `readOnlyRootFilesystem: true` the runner cannot write there, so on boot it `copytree`s the seed into the `/work` emptyDir and does all git/uv work there. Cost: one copy per pod (~hundreds of ms, measured in `warm_seconds`). Alternative was dropping the read-only root, rejected: it is the cheapest hardening we have.

## 2026-10-07 — API Gateway → SQS direct integration cannot forward the HMAC header
Plan was GitHub → API Gateway HTTP API → SQS with the `X-Hub-Signature-256` header copied into a message attribute, verified later by the controller. The SQS-SendMessage integration only accepts a *single* mapping expression or a static string per request parameter; a header reference embedded inside the MessageAttributes JSON fails with `Unable to resolve property MessageAttributes from source ...` (HTTP 400). Lowercasing the header names did not help; it is a structural limit. Fix: a 60-line Python Lambda (arm64, 128MB) in front of the queue that verifies the HMAC, drops pings/unwanted actions, and forwards accepted events with attributes. Net effect is better than the original plan: nothing unauthenticated ever reaches the queue, and the controller no longer needs the webhook secret. Cost: one more AWS resource and ~1 hour. Second gotcha: Terraform could not swap the route's integration in place (409 on delete while the route still referenced it); `state rm` + manual delete.

## 2026-10-07 — The KEDA baseline pod has to hold AWS credentials
With KEDA ScaledJob there is no dispatcher: each Job pod must `ReceiveMessage` itself, so the untrusted pod gets an IAM role (Pod Identity) and a mounted service-account token. That is exactly the trust leak the controller design removes. Keeping the baseline anyway for the cold-start comparison, with the message pull isolated in an init container so the runner image itself stays free of the AWS SDK.
