"""Baseline-only init container: pull one GitHub webhook message from SQS and turn it into a task file.

This pod holds AWS credentials, which is why the real design moves this step into the controller.
"""
import json
import os
import sys
import uuid

import boto3

queue_url = os.environ["QUEUE_URL"]
out = os.environ.get("TASK_FILE", "/shared/task.json")
sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-east-1"))

resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=10, MessageAttributeNames=["All"])
msgs = resp.get("Messages", [])
if not msgs:
    print("no message available; exiting without a task", flush=True)
    sys.exit(0)

m = msgs[0]
sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
body = json.loads(m["Body"])
action = body.get("action")
pr = body.get("pull_request") or {}
if action not in ("opened", "synchronize", "reopened") or not pr:
    print(f"ignoring event action={action!r}", flush=True)
    sys.exit(0)

task = {
    "task_id": f"baseline-{pr['number']}-{pr['head']['sha'][:8]}-{uuid.uuid4().hex[:6]}",
    "pr_number": pr["number"],
    "head_sha": pr["head"]["sha"],
    "base_sha": pr["base"]["sha"],
    "repo": body["repository"]["full_name"],
    "delivery": (m.get("MessageAttributes") or {}).get("delivery", {}).get("StringValue"),
}
with open(out, "w") as f:
    json.dump(task, f)
print(f"task written: {task['task_id']} pr#{task['pr_number']}", flush=True)
