import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """
    Triggered by SQS. Each record is an SNS notification wrapping a flag-change event.
    Raises on failure so SQS retries the message (dead-letter queue handles poison pills).
    """
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        message = json.loads(body["Message"])
        _send_slack(message)

    return {"statusCode": 200}


def _send_slack(message: dict):
    flag_name = message.get("flag", "unknown")
    event_type = message.get("event", "UNKNOWN")
    enabled = message.get("enabled", False)

    status = "ENABLED" if enabled else "DISABLED"
    text = (
        f"*Feature Flag Update*\n"
        f"Flag: `{flag_name}`\n"
        f"Event: {event_type}\n"
        f"Status: {status}"
    )

    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url=os.environ["SLACK_WEBHOOK_URL"],
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("Slack notified for flag '%s' — HTTP %s", flag_name, resp.status)
    except urllib.error.URLError as e:
        logger.error("Slack webhook failed for flag '%s': %s", flag_name, e)
        raise
