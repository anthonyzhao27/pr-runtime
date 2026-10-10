"""Webhook ingress: verify GitHub's HMAC signature, then forward the event to SQS.

Runs at the edge so nothing unauthenticated ever reaches the queue, and the cluster stays private.
Accepts events from a repo webhook or from the GitHub App (same secret). Forwards:
  - pull_request: opened / synchronize / reopened / ready_for_review
  - issue_comment: created, on a PR, mentioning the bot handle   (the "@pr-runtime" trigger)
"""
import base64
import hashlib
import hmac
import json
import os

import boto3

QUEUE_URL = os.environ["QUEUE_URL"]
SECRET = os.environ["WEBHOOK_SECRET"].encode()
MENTION = os.environ.get("MENTION_HANDLE", "@pr-runtime").lower()
PR_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
sqs = boto3.client("sqs")


def _resp(code: int, msg: str) -> dict:
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"message": msg})}


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    sig = headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return _resp(401, "bad signature")

    gh_event = headers.get("x-github-event", "")
    delivery = headers.get("x-github-delivery", "")
    if gh_event == "ping":
        return _resp(200, "pong")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _resp(400, "body is not json")
    action = payload.get("action", "")

    if gh_event == "pull_request":
        if action not in PR_ACTIONS:
            return _resp(202, f"ignored action {action}")
        if (payload.get("pull_request") or {}).get("draft") and action != "ready_for_review":
            return _resp(202, "ignored draft")
    elif gh_event == "issue_comment":
        if action != "created" or "pull_request" not in (payload.get("issue") or {}):
            return _resp(202, "ignored comment")
        if MENTION not in ((payload.get("comment") or {}).get("body") or "").lower():
            return _resp(202, "no mention")
        if ((payload.get("comment") or {}).get("user") or {}).get("type") == "Bot":
            return _resp(202, "ignored bot comment")
    else:
        return _resp(202, f"ignored event {gh_event}")

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=body,
        MessageAttributes={
            "event": {"DataType": "String", "StringValue": gh_event},
            "action": {"DataType": "String", "StringValue": action or "unknown"},
            "delivery": {"DataType": "String", "StringValue": delivery or "unknown"},
        },
    )
    return _resp(202, "queued")
