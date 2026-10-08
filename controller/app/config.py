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
    default_config: str = os.environ.get("DEFAULT_CONFIG", "full")  # full | diff_only
    admission_cap: int = _int("ADMISSION_CAP", 4)
    task_deadline: int = _int("TASK_DEADLINE_SECONDS", 300)
    runner_selector: str = os.environ.get("RUNNER_SELECTOR", "app=runner")
    runner_port: int = _int("RUNNER_PORT", 8080)
    controller_url: str = os.environ.get("CONTROLLER_URL", "http://controller.pr-runtime.svc:8000")
    post_reviews: bool = os.environ.get("POST_REVIEWS", "true").lower() == "true"
    dev: bool = os.environ.get("DEV", "0") == "1"
    read_file_max_calls: int = _int("READ_FILE_MAX_CALLS", 5)
    ignore_actions: tuple[str, ...] = field(default_factory=lambda: ("closed",))


settings = Settings()
