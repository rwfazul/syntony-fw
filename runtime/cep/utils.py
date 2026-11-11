"""Publish coordination events and stable-round closure notifications."""

import json
import time


def emit_coord_event(redis_client, event_type, base_event, meta):
    """Publish a trigger, synchronization, blocking or release event."""
    e = {
        "timestamp": time.time(),
        "tick": meta.get("tick", base_event["tick"]),
        "tick_start": meta.get("tick_start"),
        "tick_end": meta.get("tick_end"),
        "event_time": base_event["event_time"],
        "watermark": meta.get("watermark"),
        "domain": base_event["domain"],
        "base_event": base_event["event_type"],
        "coord_type": event_type,
        "priority": meta.get("priority", 5),
        "policy": meta.get("policy", "default"),
        "deadline": meta.get("deadline", None),
        "force_process": meta.get("force_process", False),
        "action_override": meta.get("action_override"),
        "participants": meta.get("participants", []),
        "blocked_action_types": meta.get("blocked_action_types", []),
        "block_ttl_seconds": meta.get("block_ttl_seconds"),
        "block_scope": meta.get("block_scope", "global"),
        "block_id": meta.get("block_id"),
        "block_domain": meta.get("block_domain") or base_event["domain"],
    }
    for field in (
        "policy_profile",
        "policy_version",
        "composition_hash",
        "evaluation_context",
        "operator_result",
    ):
        if field in meta:
            e[field] = meta[field]
    redis_client.xadd("events:coord", {"data": json.dumps(e)})


def emit_tick(redis_client, tick, tick_seconds, watermark, origin_seconds=0.0):
    event = {
        "timestamp": time.time(),
        "tick": tick,
        "tick_start": origin_seconds + (tick * tick_seconds),
        "tick_end": origin_seconds + ((tick + 1) * tick_seconds),
        "watermark": watermark,
    }
    redis_client.xadd("clock:tick", {"data": json.dumps(event)})
