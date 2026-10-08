"""Webhook ingress: verify GitHub's HMAC signature, then forward the event to SQS.

Runs at the edge so nothing unauthenticated ever reaches the queue, and the cluster stays private.
"""
import hashlib
import hmac
import json
import os

import boto3

QUEUE_URL = os.environ["QUEUE_URL"]
SECRET = os.environ["WEBHOOK_SECRET"].encode()
ACCEPT = {"pull_request"}
sqs = boto3.client("sqs")


def _resp(code: int, msg: str) -> dict:
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"message": msg})}


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()

    sig = headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return _resp(401, "bad signature")

    gh_event = headers.get("x-github-event", "")
    delivery = headers.get("x-github-delivery", "")
    if gh_event == "ping":
        return _resp(200, "pong")
    if gh_event not in ACCEPT:
        return _resp(202, f"ignored event {gh_event}")

    try:
        action = json.loads(body).get("action", "")
    except json.JSONDecodeError:
        return _resp(400, "body is not json")
    if action not in ("opened", "synchronize", "reopened"):
        return _resp(202, f"ignored action {action}")

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=body,
        MessageAttributes={
            "event": {"DataType": "String", "StringValue": gh_event},
            "action": {"DataType": "String", "StringValue": action},
            "delivery": {"DataType": "String", "StringValue": delivery or "unknown"},
        },
    )
    return _resp(202, "queued")
