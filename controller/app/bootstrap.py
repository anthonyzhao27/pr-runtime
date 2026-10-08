"""Secrets bootstrap (S9): if SECRETS_ID is set, pull the JSON secret from Secrets Manager (via Pod Identity)
and export its keys into the environment *before* settings are read. Falls back silently to whatever env
already has, so local dev with a .env keeps working."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("bootstrap")


def load_secrets() -> None:
    secret_id = os.environ.get("SECRETS_ID")
    if not secret_id:
        return
    try:
        import boto3
        sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        data = json.loads(sm.get_secret_value(SecretId=secret_id)["SecretString"])
    except Exception as e:  # noqa: BLE001
        log.error("could not load %s from Secrets Manager: %s", secret_id, e)
        return
    n = 0
    for k, v in data.items():
        if v and not os.environ.get(k):
            os.environ[k] = v
            n += 1
    if os.environ.get("POSTGRES_PASSWORD") and "$(POSTGRES_PASSWORD)" in os.environ.get("DATABASE_URL", ""):
        os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"].replace("$(POSTGRES_PASSWORD)", os.environ["POSTGRES_PASSWORD"])
    log.info("loaded %d secret(s) from %s", n, secret_id)
