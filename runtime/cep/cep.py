"""Correlate observations in stable event-time rounds and emit coordination events."""

import hashlib
import json
import math
import time
from functools import partial
from math import floor

from ..common import get_logger, get_redis_client, load_syntony_config, log_event
from ..policy_profiles import ACTIVE_PROFILE, PROFILE_REQUESTS, PolicyProfiles
from .expressions import OperatorRuntime
from .patterns import get_patterns
from .utils import emit_coord_event, emit_tick

logger = get_logger("CEP")
ROUND_CHECKPOINT_KEY = "syntony:cep:round_checkpoint"


def event_time_to_round(event_time, round_seconds, origin_seconds=0.0):
    """Return the control-round index for an event timestamp."""
    return int(floor((event_time - origin_seconds) / round_seconds))


def watermark_to_stable_round(watermark, round_seconds, origin_seconds=0.0):
    """Return the latest round whose end is at or below the watermark."""
    if watermark == float("-inf"):
        return -1
    return int(floor((watermark - origin_seconds) / round_seconds)) - 1


def effective_watermark(domain_watermarks, policy, required_domains):
    """Combines source watermarks according to the configured CEP policy."""
    if policy != "participant_min":
        return max(domain_watermarks.values(), default=float("-inf"))
    if not required_domains or any(domain not in domain_watermarks for domain in required_domains):
        return float("-inf")
    return min(domain_watermarks[domain] for domain in required_domains)


def initial_round_position(event_times, round_seconds, relative):
    """Initialize the first control round from the earliest available event-time input."""
    first_time = min(event_times)
    origin = floor(first_time / round_seconds) * round_seconds if relative else 0.0
    return origin, event_time_to_round(first_time, round_seconds, origin) - 1


def pattern_anchor(pattern, events, tick):
    """Choose provenance from matching retained context, never an open round."""
    first_tick = tick - pattern.get("window", 1) + 1
    relevant = [event for event in events if first_tick <= event["tick"] <= tick]
    domain, event_type = pattern.get("domain"), pattern.get("event_type")
    if domain is not None:
        relevant = [event for event in relevant if event["domain"] == domain]
    if event_type is not None:
        relevant = [
            event
            for event in relevant
            if event["event_type"] == event_type
            and all(event.get(k) == v for k, v in pattern.get("where", {}).items())
        ]
    elif pattern.get("participants"):
        from .patterns import _matches_participant

        relevant = [
            event
            for event in relevant
            if any(
                _matches_participant(event, participant) for participant in pattern["participants"]
            )
        ]
    return (
        max(relevant, key=lambda event: (event["tick"], event["event_time"])) if relevant else None
    )


def close_stable_rounds(
    redis_client,
    patterns,
    round_table,
    round_seconds,
    retention_rounds,
    global_watermark,
    last_closed_round,
    fallback_event=None,  # noqa: ARG001 - Retained for callers of the round closure API.
    round_origin_seconds=0.0,
    before_round=None,
    operators=None,
):
    """Close newly stable rounds, evaluate patterns and prune the Round Table."""
    if operators is None and any("operator" in pattern for pattern in patterns):
        raise ValueError("Pass one OperatorRuntime across operator round evaluations")
    stable_round = watermark_to_stable_round(
        global_watermark,
        round_seconds,
        origin_seconds=round_origin_seconds,
    )
    while last_closed_round < stable_round:
        last_closed_round += 1
        tick_start = round_origin_seconds + (last_closed_round * round_seconds)
        tick_end = (last_closed_round + 1) * round_seconds
        tick_end += round_origin_seconds
        profile_fields = before_round(last_closed_round, tick_start) if before_round else {}
        emit_tick(
            redis_client,
            last_closed_round,
            round_seconds,
            global_watermark,
            origin_seconds=round_origin_seconds,
        )
        log_event(
            logger,
            "info",
            "tick_closed",
            tick=last_closed_round,
            tick_start=round(tick_start, 6),
            tick_end=round(tick_end, 6),
            watermark=round(global_watermark, 6),
        )

        rounds_to_check = [
            round_index
            for round_index in round_table
            if round_index <= last_closed_round
            and round_index >= max(0, last_closed_round - retention_rounds + 1)
        ]
        all_events = []
        for round_index in rounds_to_check:
            all_events.extend(round_table[round_index])

        if operators is not None:
            operators.begin_round(
                redis_client,
                [pattern for pattern in patterns if "operator" in pattern],
                last_closed_round,
                retention_rounds,
                profile_fields,
            )
        for pattern in patterns:
            if "operator" in pattern:
                evaluations = operators.evaluate(
                    redis_client, pattern, round_table, last_closed_round, tick_end
                )
            else:
                if not pattern["match"](all_events, round_table, last_closed_round):
                    continue
                base_event = pattern_anchor(pattern, all_events, last_closed_round)
                if not base_event:
                    continue
                evaluations = [{"anchor": base_event}]
            for evaluation in evaluations:
                base_event = evaluation["anchor"]
                meta = {
                    **profile_fields,
                    "priority": pattern["priority"],
                    "policy": pattern["name"],
                    "deadline": (time.time() + pattern["deadline_seconds"])
                    if pattern.get("deadline_seconds") is not None
                    else None,
                    "tick": last_closed_round,
                    "tick_start": tick_start,
                    "tick_end": tick_end,
                    "watermark": global_watermark,
                    "force_process": pattern.get("force_process", False),
                    "action_override": pattern.get("action_override"),
                    "participants": pattern.get("participants", []),
                    "blocked_action_types": pattern.get("blocked_action_types", []),
                    "block_ttl_seconds": pattern.get("block_ttl_seconds"),
                    "block_scope": pattern.get("block_scope", "global"),
                    "block_id": pattern.get("block_id"),
                    "block_domain": pattern.get("block_domain"),
                }
                if "operator" in pattern:
                    meta["evaluation_context"] = {
                        "source": "stable_round",
                        "round": last_closed_round,
                    }
                    if "operator_result" in evaluation:
                        meta["operator_result"] = evaluation["operator_result"]
                emit_coord_event(redis_client, pattern["coord_type"], base_event, meta)
                log_event(
                    logger,
                    "info",
                    "coord_event_emitted",
                    domain=base_event["domain"],
                    tick=last_closed_round,
                    policy=pattern["name"],
                    coord_type=pattern["coord_type"],
                    watermark=round(global_watermark, 6),
                    force_process=meta["force_process"],
                )

        for old_round in list(round_table.keys()):
            if old_round < last_closed_round - retention_rounds:
                del round_table[old_round]

    return last_closed_round


def cep_loop():
    """Read observations and watermarks, close stable rounds and evaluate patterns."""
    redis_client = get_redis_client()
    config = load_syntony_config()
    cep_cfg = config.get("cep", {})
    round_cfg = cep_cfg.get("tick", {})
    round_seconds = float(round_cfg.get("seconds", 1))
    retention_rounds = int(round_cfg.get("retention_ticks", 8))
    patterns = get_patterns()
    window_rounds = max((pattern["window"] for pattern in patterns), default=1)
    round_table = {}
    watermark_policy = cep_cfg["watermark_policy"]
    required_watermark_domains = cep_cfg.get("watermark_domains", [])
    if watermark_policy == "participant_min" and not required_watermark_domains:
        required_watermark_domains = sorted(
            {
                participant.get("domain")
                for pattern in patterns
                for participant in pattern.get("participants", [])
                if participant.get("domain")
            }
        )
    domain_watermarks = {}
    current_watermark = float("-inf")
    relative_round_origin = bool(cep_cfg.get("relative_tick_origin", False))
    round_origin_seconds = None if relative_round_origin else 0.0
    last_closed_round = -1
    initialized = False
    last_ids = {
        "events:obs": "0-0",
        "clock:watermark": "0-0",
    }

    checkpoint_config = cep_cfg
    if cep_cfg.get("policy_profiles"):
        checkpoint_config = {
            "cep": cep_cfg,
            "coordinator": config["coordinator"],
            "actions": config["executor"]["actions"],
        }
    config_digest = hashlib.sha256(
        json.dumps(checkpoint_config, sort_keys=True).encode()
    ).hexdigest()
    checkpoint = redis_client.get(ROUND_CHECKPOINT_KEY)
    saved = {}
    if checkpoint is not None:
        saved = json.loads(checkpoint)
        if saved["version"] != 1 or saved["config_digest"] != config_digest:
            raise ValueError("CEP checkpoint is incompatible with this configuration")
        round_table = {int(index): events for index, events in saved["round_table"].items()}
        last_ids = saved["stream_cursors"]
        domain_watermarks = saved["domain_watermarks"]
        round_origin_seconds = saved["round_origin_seconds"]
        last_closed_round = saved["last_closed_round"]
        initialized = saved["initialized"]
        current_watermark = effective_watermark(
            domain_watermarks, watermark_policy, required_watermark_domains
        )

    operators = None
    if any("operator" in pattern for pattern in patterns):
        from ..internal_state.control import get_flag_context

        operators = OperatorRuntime(
            saved.get("operator_state"),
            state_reader=partial(get_flag_context, redis_client, config),
        )

    profiles = None
    if cep_cfg.get("policy_profiles"):
        profiles = PolicyProfiles(config, patterns, saved)
        last_ids.setdefault(PROFILE_REQUESTS, "0-0")

    log_event(
        logger,
        "info",
        "cep_started",
        round_seconds=round_seconds,
        window_rounds=window_rounds,
        retention_rounds=retention_rounds,
        watermark_policy=watermark_policy,
        watermark_domains=required_watermark_domains,
        relative_round_origin=relative_round_origin,
        patterns=[pattern["name"] for pattern in patterns],
    )

    while True:
        resp = redis_client.xread(last_ids, block=int(round_seconds * 1000), count=None)
        if not resp:
            continue

        with redis_client.pipeline() as batch:
            batch.watch(ROUND_CHECKPOINT_KEY)
            if batch.get(ROUND_CHECKPOINT_KEY) != checkpoint:
                raise RuntimeError("CEP checkpoint advanced in another process")
            batch.multi()
            before_round = None
            if profiles:
                profiles.acknowledged.clear()
                previous_profile = profiles.state
                before_round = partial(
                    profiles.before_round, redis_client, batch, patterns, round_table
                )
            # Use the earliest timestamp in the initial batch as the round frontier.
            if not initialized:
                times = []
                for stream_name, rows in resp:
                    if stream_name == PROFILE_REQUESTS:
                        continue
                    for _, fields in rows:
                        try:
                            value = json.loads(fields["data"])
                            if not isinstance(value, dict):
                                continue
                            timestamp = (
                                value.get("event_time")
                                if stream_name == "events:obs"
                                else value.get("watermark")
                            )
                            if (
                                value.get("domain") in required_watermark_domains
                                and isinstance(timestamp, (int, float))
                                and math.isfinite(timestamp)
                            ):
                                times.append(timestamp)
                        except (KeyError, TypeError, ValueError):
                            pass
                if times:
                    round_origin_seconds, last_closed_round = initial_round_position(
                        times, round_seconds, relative_round_origin
                    )
                    initialized = True

            for stream_name, events in sorted(resp, key=lambda pair: pair[0] == "clock:watermark"):
                for eid, event_data in events:
                    last_ids[stream_name] = eid
                    try:
                        event = json.loads(event_data["data"])
                        if not isinstance(event, dict):
                            raise ValueError("Event envelope must be a JSON object")
                        if stream_name == PROFILE_REQUESTS:
                            profiles.enqueue(event)
                            continue
                        stamp = (
                            event.get("watermark")
                            if stream_name == "clock:watermark"
                            else event.get("event_time")
                        )
                        if (
                            not isinstance(stamp, (int, float))
                            or not math.isfinite(stamp)
                            or event.get("domain") not in required_watermark_domains
                        ):
                            raise ValueError("Invalid timestamp or unknown domain")
                        if stream_name == "events:obs" and not isinstance(
                            event.get("event_type"), str
                        ):
                            raise ValueError("Missing observed-event type")
                    except (KeyError, TypeError, ValueError) as error:
                        log_event(
                            logger,
                            "warning",
                            "input_ignored",
                            stream=stream_name,
                            record_id=eid,
                            reason=str(error),
                        )
                        continue

                    if stream_name == "clock:watermark":
                        watermark_value = event.get("watermark", float("-inf"))
                        watermark_domain = event.get("domain")
                        if watermark_domain:
                            domain_watermarks[watermark_domain] = max(
                                domain_watermarks.get(watermark_domain, float("-inf")),
                                watermark_value,
                            )
                        current_watermark = effective_watermark(
                            domain_watermarks,
                            watermark_policy,
                            required_watermark_domains,
                        )
                        if round_origin_seconds is None and current_watermark != float("-inf"):
                            round_origin_seconds = (
                                floor(current_watermark / round_seconds) * round_seconds
                            )
                        log_event(
                            logger,
                            "info",
                            "watermark_observed",
                            domain=event.get("domain"),
                            watermark=round(current_watermark, 6),
                            source_watermark=round(watermark_value, 6),
                            watermark_policy=watermark_policy,
                        )
                        if current_watermark == float("-inf"):
                            continue
                        last_closed_round = close_stable_rounds(
                            batch,
                            patterns,
                            round_table,
                            round_seconds,
                            retention_rounds,
                            current_watermark,
                            last_closed_round,
                            round_origin_seconds=round_origin_seconds or 0.0,
                            before_round=before_round,
                            operators=operators,
                        )
                        continue

                    if "event_time" not in event or "domain" not in event:
                        log_event(
                            logger,
                            "info",
                            "obs_event_ignored",
                            reason="missing_event_time_or_domain",
                            event_type=event.get("event_type"),
                        )
                        continue

                    domain = event["domain"]
                    if watermark_policy != "participant_min":
                        current_watermark = max(
                            current_watermark,
                            event.get("watermark", float("-inf")),
                        )
                    if round_origin_seconds is None:
                        round_origin_seconds = (
                            floor(event["event_time"] / round_seconds) * round_seconds
                        )
                    origin_seconds = round_origin_seconds or 0.0
                    event_round = event_time_to_round(
                        event["event_time"],
                        round_seconds,
                        origin_seconds=origin_seconds,
                    )
                    if profiles and event_round < profiles.state.get(
                        "activation_round", float("-inf")
                    ):
                        continue
                    event["tick"] = event_round
                    round_table.setdefault(event_round, []).append(event)
                    log_event(
                        logger,
                        "info",
                        "obs_event_registered",
                        domain=domain,
                        event_type=event["event_type"],
                        tick=event_round,
                        event_time=round(event["event_time"], 6),
                        watermark=round(current_watermark, 6)
                        if current_watermark != float("-inf")
                        else "neg_inf",
                        force_process=event.get("force_process", False),
                    )

                    if event.get("force_process") and event_round <= last_closed_round:
                        for pattern in patterns:
                            if not pattern.get("force_process"):
                                continue
                            if not pattern["match"](
                                round_table.get(event_round, []), round_table, event_round
                            ):
                                continue
                            meta = {
                                **(profiles.state if profiles else {}),
                                "priority": pattern["priority"],
                                "policy": pattern["name"],
                                "deadline": (time.time() + pattern["deadline_seconds"])
                                if pattern.get("deadline_seconds") is not None
                                else None,
                                "tick": event_round,
                                "tick_start": origin_seconds + event_round * round_seconds,
                                "tick_end": origin_seconds + (event_round + 1) * round_seconds,
                                "watermark": current_watermark,
                                "force_process": True,
                                "action_override": pattern.get("action_override"),
                                "participants": pattern.get("participants", []),
                                "blocked_action_types": pattern.get("blocked_action_types", []),
                                "block_ttl_seconds": pattern.get("block_ttl_seconds"),
                                "block_scope": pattern.get("block_scope", "global"),
                                "block_id": pattern.get("block_id"),
                                "block_domain": pattern.get("block_domain"),
                            }
                            emit_coord_event(batch, pattern["coord_type"], event, meta)
                            log_event(
                                logger,
                                "info",
                                "forced_coord_event_emitted",
                                domain=domain,
                                tick=event_round,
                                policy=pattern["name"],
                                coord_type=pattern["coord_type"],
                                watermark=round(current_watermark, 6)
                                if current_watermark != float("-inf")
                                else "neg_inf",
                                force_process=True,
                            )

                    last_closed_round = close_stable_rounds(
                        batch,
                        patterns,
                        round_table,
                        round_seconds,
                        retention_rounds,
                        current_watermark,
                        last_closed_round,
                        round_origin_seconds=origin_seconds,
                        before_round=before_round,
                        operators=operators,
                    )

            next_checkpoint = json.dumps(
                {
                    "version": 1,
                    "config_digest": config_digest,
                    "round_table": round_table,
                    "stream_cursors": last_ids,
                    "domain_watermarks": domain_watermarks,
                    "round_origin_seconds": round_origin_seconds,
                    "last_closed_round": last_closed_round,
                    "initialized": initialized,
                    **({"operator_state": operators.checkpoint()} if operators else {}),
                    **(
                        {"active_profile": profiles.state, "profile_requests": profiles.pending}
                        if profiles
                        else {}
                    ),
                },
            )
            if profiles and (checkpoint is None or profiles.state != previous_profile):
                batch.set(ACTIVE_PROFILE, json.dumps(profiles.state))
            batch.set(ROUND_CHECKPOINT_KEY, next_checkpoint)
            batch.execute()
            checkpoint = next_checkpoint


# Keep the previous Python names available to existing callers.
event_time_to_tick = event_time_to_round
watermark_to_stable_tick = watermark_to_stable_round
close_stable_ticks = close_stable_rounds


if __name__ == "__main__":
    try:
        cep_loop()
    except KeyboardInterrupt:
        log_event(logger, "info", "cep_stopped")
