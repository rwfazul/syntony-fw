"""Publish correlated execution outcomes as observed feedback events."""

import json
import time


def emit_feedback(redis_client, kind, payload):
    """Publishes one executor outcome as an ``E_fed`` event."""
    event = {
        "timestamp": time.time(),
        "domain": payload.get("domain"),
        "event_type": "E_fed",
        "feedback_type": kind,
        "event_time": payload.get("event_time", time.time()),
        "processing_time": time.time(),
        "payload": payload,
    }
    redis_client.xadd("events:obs", {"data": json.dumps(event)})
