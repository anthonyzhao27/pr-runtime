from prometheus_client import Counter, Gauge, Histogram

PHASE_BUCKETS = (0.5, 1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300)

tasks_pending = Gauge("prr_tasks_pending", "Tasks admitted by the controller but waiting for a runner")
runners_idle = Gauge("prr_runners_idle", "Warm runner pods ready and unassigned")
reviews_in_flight = Gauge("prr_reviews_in_flight", "Tasks in the LLM review stage (queued + running)")
runners_busy = Gauge("prr_runners_busy", "Runner pods currently executing a task")
admission_rejects = Counter("prr_admission_rejects_total", "Scheduler ticks where work waited because busy >= cap")
tasks_total = Counter("prr_tasks_total", "Tasks by terminal state", ["state"])
events_total = Counter("prr_webhook_events_total", "Webhook events consumed from SQS", ["action"])
phase_seconds = Histogram("prr_phase_seconds", "Per-phase durations", ["phase"], buckets=PHASE_BUCKETS)
time_to_comment = Histogram("prr_time_to_comment_seconds", "Webhook received -> review posted", buckets=PHASE_BUCKETS)
wait_for_runner = Histogram("prr_wait_for_runner_seconds", "Admitted -> assigned to a runner", buckets=PHASE_BUCKETS)
cold_assignments = Counter("prr_cold_assignments_total", "Tasks assigned to a runner that was not warm (pod younger than 10s)")
llm_tokens = Counter("prr_llm_tokens_total", "Tokens used by the reviewer", ["direction"])
cost_usd = Counter("prr_cost_usd_total", "Accumulated cost in USD", ["kind"])  # compute | tokens
tasks_lost = Counter("prr_tasks_lost_total", "Busy tasks requeued because the runner died or timed out", ["reason"])
