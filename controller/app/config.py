from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    namespace: str = os.environ.get("NAMESPACE", "pr-runtime")
    queue_url: str = os.environ.get("QUEUE_URL", "")
    aws_region: str = os.environ.get("AWS_REGION", "us-east-1")
    database_url: str = os.environ.get("DATABASE_URL", "")
    repo: str = os.environ.get("REPO", "anthonyzhao27/flask")
    github_token: str = os.environ.get("GITHUB_BOT_TOKEN", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    reviewer_model: str = os.environ.get("REVIEWER_MODEL", "")
    judge_model: str = os.environ.get("JUDGE_MODEL", "")
    reasoning_effort: str = os.environ.get("REASONING_EFFORT", "high")  # low | medium | high | xhigh
    default_config: str = os.environ.get("DEFAULT_CONFIG", "full")  # full | diff_only
    admission_cap: int = _int("ADMISSION_CAP", 4)
    review_workers: int = _int("REVIEW_WORKERS", 12)  # LLM stage is I/O bound; separate knob from the runner cap
    task_deadline: int = _int("TASK_DEADLINE_SECONDS", 300)
    runner_selector: str = os.environ.get("RUNNER_SELECTOR", "app=runner")
    runner_port: int = _int("RUNNER_PORT", 8080)
    controller_url: str = os.environ.get("CONTROLLER_URL", "http://controller.pr-runtime.svc:8000")
    post_reviews: bool = os.environ.get("POST_REVIEWS", "true").lower() == "true"
    dev: bool = os.environ.get("DEV", "0") == "1"
    read_file_max_calls: int = _int("READ_FILE_MAX_CALLS", 5)
    agent_max_tool_calls: int = _int("AGENT_MAX_TOOL_CALLS", 15)
    ignore_actions: tuple[str, ...] = field(default_factory=lambda: ("closed",))
    # Cost model (S5). Prices are USD per 1M tokens; node price USD/hour; a runner is charged its CPU-limit share of a node.
    price_input_per_m: float = float(os.environ.get("PRICE_INPUT_PER_M", "10.0"))    # gpt-6-astra standard tier
    price_output_per_m: float = float(os.environ.get("PRICE_OUTPUT_PER_M", "50.0"))
    node_usd_per_hour: float = float(os.environ.get("NODE_USD_PER_HOUR", "0.0816"))  # m7g.large on-demand us-east-1
    node_vcpu: float = float(os.environ.get("NODE_VCPU", "2"))
    runner_cpu_limit: float = float(os.environ.get("RUNNER_CPU_LIMIT", "1"))
    pool_size: int = _int("POOL_SIZE", 4)
    guidelines_dir: str = os.environ.get("GUIDELINES_DIR", "/guidelines")
    # Multi-repo (S8): events from repos not listed here are ignored (or answered with a "not configured" comment).
    repos: tuple[str, ...] = tuple(r.strip() for r in os.environ.get("REPOS", "").split(",") if r.strip())  # empty = any installed repo

    def repo_allowed(self, repo: str) -> bool:
        return not self.repos or repo in self.repos
    mention_handle: str = os.environ.get("MENTION_HANDLE", "@pr-runtime")
    checks_enabled: bool = os.environ.get("CHECKS_ENABLED", "true").lower() == "true"

    def compute_usd(self, seconds: float) -> float:
        return seconds * (self.node_usd_per_hour / 3600.0) * (self.runner_cpu_limit / self.node_vcpu)

    def tokens_usd(self, tokens_in: int, tokens_out: int) -> float:
        return tokens_in / 1e6 * self.price_input_per_m + tokens_out / 1e6 * self.price_output_per_m

    def pool_standing_usd_per_hour(self) -> float:
        return self.pool_size * self.node_usd_per_hour * (self.runner_cpu_limit / self.node_vcpu)


settings = Settings()
