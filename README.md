# pr-runtime

Pod-per-PR code review runtime on EKS. Every pull request gets an ephemeral, secret-less pod that clones
the branch and runs the test suite; a trusted controller admits work from a queue, keeps a warm pool so
nothing waits on a cold start, asks an LLM to review the diff with the test evidence attached, and posts
inline review comments back to GitHub. An eval harness with real and synthetic injected bugs measures
whether the reviewer catches what CI misses.

```
GitHub webhook → API Gateway → Lambda (HMAC) → SQS → controller ─┬─► warm runner pool (pytest, ruff)
                                                                  ├─► LLM review → GitHub review
                                                                  └─► Postgres → console (React) / Prometheus → Grafana
```

- `SPEC.md` — design, decisions table, schedule, stretch list
- `docs/ONBOARDING.md` — the Kubernetes / AWS / networking primer to read first
- `docs/DIAGRAMS.md` — cluster internals and whole-system Mermaid diagrams
- `docs/DECISIONS.md` — what broke and why
- `docs/TALK.md` — talk outline and Q&A prep
- `infra/` Terraform · `controller/` FastAPI control plane · `runner/` untrusted executor · `console/` React UI
- `deploy/chart` Helm chart · `deploy/baseline` KEDA cold-start baseline · `eval/` corpus, runner, scorer
- `dashboards/` Grafana

## Run

```bash
cd infra && terraform apply                       # VPC, EKS (Graviton), SQS, Lambda ingress, ECR, Pod Identity
$(terraform output -raw update_kubeconfig)
scripts/sync_secret.sh                            # .env -> k8s Secret (OPENAI_API_KEY, GITHUB_BOT_TOKEN, ...)
scripts/create_webhook.sh                         # GitHub webhook on the fork -> API Gateway
helm upgrade --install pr-runtime deploy/chart -n pr-runtime
kubectl port-forward -n pr-runtime svc/controller 18000:8000   # console at http://localhost:18000
scripts/open_prs.py 20                            # burst
eval/run_eval.py && eval/score.py eval/results/latest
```
