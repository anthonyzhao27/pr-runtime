# pr-runtime console

React 18 + Vite + TypeScript SPA served by the FastAPI controller. Three routes:

- `/` — live task table (SSE via `/api/events`), state/PR filters, scheduler stats strip
- `/tasks/:id` — task detail: findings with thumbs up/down feedback, diff, pytest/ruff output, re-run
- `/eval` — context-config ablation from `eval/results/latest/summary.json`

Infra health (queue depth, pod counts) lives in Grafana, not here.

## Dev

```sh
cd console
npm install
kubectl -n pr-runtime port-forward svc/controller 18000:8000   # in another shell
npm run dev                                                      # http://localhost:5173
```

`vite.config.ts` proxies `/api` and `/eval` to `http://localhost:18000`.

## Build

```sh
npm run build        # tsc -b && vite build  ->  ../controller/static/
```

Output goes to `controller/static/` (`index.html` + `assets/*`), which the controller serves:
`/assets/*` statically, everything else falls through to `index.html`. The controller Dockerfile
picks the directory up as-is, so build before building the image.

`npm run typecheck` runs the TypeScript project build without emitting.
