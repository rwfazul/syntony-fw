"""Prioritize coordination events, manage blocking guards and admit action plans."""

import os
from heapq import heappop, heappush

from ..common import get_logger, get_redis_client, log_event
from ..internal_state.control import admit_plan
from ..policy_profiles import profile_matches
from .internal_state import (
    check_constraints,
    register_action_block,
    register_dedupe,
    release_action_block,
    update_state,
)
from .policies import map_to_actions
from .utils import parse_coord_event

logger = get_logger("Coordinator")


def coordinator_loop():
    """Consumes coordination events and dispatches executable actions.

    Coordination events are first queued by priority, then checked against
    constraints such as active blocks, expired deadlines, and dedupe windows.
    Only allowed events are mapped to actions and forwarded to the executor
    command stream.
    """
    redis_client = get_redis_client()
    poll_interval = float(os.getenv("POLL_INTERVAL", "0.5"))
    from ..streams import read_batches

    pq = []
    processed_key = "coordinator:processed"

    log_event(logger, "info", "coordinator_started", poll_interval=poll_interval)

    for batch in read_batches(redis_client, "events:coord", "coordinator", poll_interval):
        for eid, e_raw in batch:
            event = parse_coord_event(e_raw)
            event.setdefault("coordination_event_id", eid)
            priority = event.get("priority", 5)
            heappush(pq, (priority, eid, event))
            log_event(
                logger,
                "info",
                "coord_event_queued",
                domain=event["domain"],
                coord_type=event["coord_type"],
                policy=event["policy"],
                priority=priority,
                force_process=event.get("force_process", False),
            )

        while pq:
            priority, eid, event = heappop(pq)
            uid = f"{eid}:{event.get('policy')}"
            if redis_client.sismember(processed_key, uid):
                redis_client.xack("events:coord", "coordinator", eid)
                continue

            log_event(
                logger,
                "info",
                "coord_event_processing",
                domain=event["domain"],
                tick=event.get("tick"),
                coord_type=event["coord_type"],
                policy=event["policy"],
                priority=priority,
                force_process=event.get("force_process", False),
            )

            is_guard_event = event.get("coord_type") in {"E_block", "E_unblock"}
            allowed = (
                profile_matches(redis_client, event) and check_constraints(event, redis_client)
                if is_guard_event
                else True
            )
            if allowed:
                if event.get("coord_type") == "E_unblock":
                    removed = release_action_block(event, redis_client)
                    register_dedupe(event, redis_client)
                    update_state(
                        redis_client,
                        eid,
                        "released" if removed else "release_noop",
                        tick=event.get("tick"),
                    )
                    log_event(
                        logger,
                        "info",
                        "action_guard_released",
                        domain=event["block_domain"],
                        block_id=event["block_id"],
                        removed=removed,
                        policy=event["policy"],
                    )
                    redis_client.xack("events:coord", "coordinator", eid)
                    continue
                if event.get("coord_type") == "E_block" and event.get("blocked_action_types"):
                    block_record = register_action_block(event, redis_client)
                    register_dedupe(event, redis_client)
                    update_state(redis_client, eid, "active_block", tick=event.get("tick"))
                    log_event(
                        logger,
                        "info",
                        "action_classes_blocked",
                        domain=event["domain"],
                        policy=event.get("policy"),
                        blocked_action_types=event.get("blocked_action_types"),
                        block_scope=event.get("block_scope", "global"),
                        expires_at=block_record.get("expires_at") if block_record else None,
                    )
                    redis_client.sadd(processed_key, uid)
                    redis_client.xack("events:coord", "coordinator", eid)
                    continue
                actions = map_to_actions(event)
                ticket_id = event.get("ticket_id") or eid
                for action_index, action in enumerate(actions, start=1):
                    action.setdefault("coordination_event_id", eid)
                    action.setdefault("ticket_id", ticket_id)
                    action.setdefault("action_id", f"{ticket_id}:{action_index}")
                admitted, reason = admit_plan(redis_client, event, actions)
                update_state(redis_client, eid, reason, actions, tick=event.get("tick"))
                log_event(
                    logger,
                    "info" if admitted else "warning",
                    "action_plan_decision",
                    domain=event["domain"],
                    policy=event["policy"],
                    ticket_id=ticket_id,
                    reason=reason,
                    action_count=len(actions),
                )
            else:
                update_state(redis_client, eid, "blocked", tick=event.get("tick"))
                log_event(
                    logger,
                    "warning",
                    "coord_event_blocked",
                    domain=event["domain"],
                    tick=event.get("tick"),
                    policy=event["policy"],
                    coord_type=event["coord_type"],
                    force_process=event.get("force_process", False),
                )

            redis_client.sadd(processed_key, uid)
            redis_client.xack("events:coord", "coordinator", eid)


if __name__ == "__main__":
    try:
        coordinator_loop()
    except KeyboardInterrupt:
        log_event(logger, "info", "coordinator_stopped")
