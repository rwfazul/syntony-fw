"""Parse metric envelopes and publish observations, watermarks and lateness diagnostics."""

import json
import math
import time
import uuid


def parse_metric_event(event):
    """Parses one raw metric envelope from `metrics:<domain>`.

    The payload is expected to preserve both `event_time` and
    `processing_time`, which lets downstream stages diagnose lateness without
    losing the original source timestamp.
    """
    data = json.loads(event["data"])
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("domain"), str)
        or not isinstance(data.get("metric"), dict)
    ):
        raise ValueError("Metric envelope requires domain and metric object")
    metric = data["metric"]
    if not isinstance(metric.get("metric"), str) or not metric["metric"]:
        raise ValueError("Metric name is required")
    for name, value in [
        ("event_time", data.get("event_time")),
        ("processing_time", data.get("processing_time")),
        ("metric value", metric.get("value")),
    ]:
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if type(data.get("skip_watermark_check", False)) is not bool:
        raise ValueError("skip_watermark_check must be a JSON boolean")
    return data


def emit_obs_event(rule, metric_event, redis_client, watermark_state):
    """Publishes one interpreted observed event to `events:obs`.

    This payload becomes the contract between the Composer and the CEP. Besides
    the logical event type, it includes watermark context and provenance fields
    so CEP and later stages can reason about both timing and origin.
    """
    e_obs = {
        "timestamp": time.time(),
        "domain": metric_event["domain"],
        "event_type": rule["name"],
        "rule_type": rule.get("type"),
        "priority": rule.get("priority", 5),
        "origin_metric": metric_event["metric"]["metric"],
        "origin_value": metric_event["metric"]["value"],
        "event_time": metric_event["event_time"],
        "processing_time": metric_event["processing_time"],
        "watermark": watermark_state["watermark"],
        "watermark_before": watermark_state["watermark_before"],
        "lateness_seconds": watermark_state["lateness_seconds"],
        "late": watermark_state["late"],
        "accepted": watermark_state["accepted"],
        "sequence": metric_event.get("sequence"),
        "force_process": rule.get("force_process", False),
        "skip_watermark_check": metric_event.get("skip_watermark_check", False)
        or rule.get("skip_watermark_check", False),
        "metadata": {
            **metric_event.get("metadata", {}),
            **rule.get("metadata", {}),
        },
    }
    redis_client.xadd("events:obs", {"data": json.dumps(e_obs)})
    return e_obs


def emit_late_metric(metric_event, redis_client, watermark_state):
    """Publishes a rejected late metric for observability and validation."""
    event = {
        "timestamp": time.time(),
        "domain": metric_event["domain"],
        "event_type": "late_metric",
        "metric": metric_event["metric"],
        "event_time": metric_event["event_time"],
        "processing_time": metric_event["processing_time"],
        "watermark": watermark_state["watermark"],
        "watermark_before": watermark_state["watermark_before"],
        "lateness_seconds": watermark_state["lateness_seconds"],
        "accepted": False,
        "sequence": metric_event.get("sequence"),
    }
    redis_client.xadd("events:late", {"data": json.dumps(event)})


def emit_watermark(redis_client, domain, watermark_state):
    """Publishes the latest watermark estimate to `clock:watermark`."""
    event = {
        "timestamp": time.time(),
        "domain": domain,
        "watermark": watermark_state["watermark"],
        "max_event_time_seen": watermark_state["max_event_time_seen"],
        "allowed_lateness": watermark_state["allowed_lateness"],
        "watermark_delay": watermark_state["watermark_delay"],
        "late_violation_streak": watermark_state["late_violation_streak"],
    }
    redis_client.xadd("clock:watermark", {"data": json.dumps(event)})


def emit_clock_resync_request(redis_client, domain, reason, details):
    """Requests a source clock resync after repeated watermark violations."""
    event = {
        "request_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "domain": domain,
        "reason": reason,
        "details": details,
    }
    redis_client.xadd("control:clock", {"data": json.dumps(event)})
