"""Extract JSON fields and publish raw metric envelopes to domain streams."""

import json
import time


def _extract_path(payload, path, default=None):
    if path in (None, "", "$"):
        return payload

    current = payload
    for token in str(path).split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return default
    return current


def publish_metric(
    r,
    domain,
    metric_name,
    value,
    event_time,
    processing_time,
    sequence,
    skip_watermark_check=False,
    metadata=None,
):
    """Publishes a metric to Redis Stream."""
    event = {
        "domain": domain,
        "metric": {
            "metric": metric_name,
            "value": value,
        },
        "event_time": event_time,
        "processing_time": processing_time,
        "sequence": sequence,
        "emitted_at": time.time(),
        "skip_watermark_check": skip_watermark_check,
        "metadata": metadata or {},
    }
    r.xadd(f"metrics:{domain}", {"data": json.dumps(event)})
