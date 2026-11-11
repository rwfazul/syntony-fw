"""Compile event patterns and match domain-qualified participants in stable rounds."""

from ..common import load_syntony_config
from ..configuration import normalize_pattern_rounds
from .expressions import compile_operator


def _matches_configured_fields(event, expected_fields):
    return all(event.get(field) == expected for field, expected in expected_fields.items())


def _matches_participant(event, participant):
    return (
        event.get("event_type") == participant.get("event_type")
        and participant.get("domain") in (None, event.get("domain"))
        and _matches_configured_fields(event, participant.get("where", {}))
    )


def _compile_pattern(pattern_cfg):
    pattern_cfg = normalize_pattern_rounds(pattern_cfg)
    pattern_type = pattern_cfg.get("type")
    operator = None
    match = None

    if pattern_type == "count_event":

        def match(events, round_table, tick, cfg=pattern_cfg):  # noqa: ARG001 - Shared pattern matcher signature.
            round_index = tick
            minimum_round = round_index - cfg.get("within_ticks", 1) + 1
            relevant = [
                event for event in events if minimum_round <= event.get("tick", 0) <= round_index
            ]
            count = sum(
                1
                for event in relevant
                if event["event_type"] == cfg["event_type"]
                and cfg.get("domain") in (None, event.get("domain"))
                and _matches_configured_fields(event, cfg.get("where", {}))
            )
            return count >= cfg["count"]
    elif pattern_type in ("same_round_all", "same_tick_all"):

        def match(events, round_table, tick, cfg=pattern_cfg):  # noqa: ARG001 - Shared pattern matcher signature.
            round_index = tick
            types = {
                event["event_type"]
                for event in round_table.get(round_index, [])
                if cfg.get("domain") in (None, event.get("domain"))
            }
            return all(event_type in types for event_type in cfg.get("event_types", []))
    elif pattern_type == "participants_all":

        def match(events, round_table, tick, cfg=pattern_cfg):
            round_index = tick
            window_rounds = max(1, int(cfg.get("within_ticks", 1)))
            minimum_round = round_index - window_rounds + 1
            relevant = [
                event
                for event in events
                if minimum_round <= event.get("tick", round_index) <= round_index
            ]
            # A prohibited participant vetoes the match within this stable window.
            if any(
                _matches_participant(event, participant)
                for event in relevant
                for participant in cfg.get("forbidden_participants", [])
            ):
                return False
            current_round = round_table.get(round_index, [])
            participants = cfg.get("participants", [])
            if not participants:
                return False
            if cfg.get("require_current_tick", True) and not any(
                _matches_participant(event, participant)
                for event in current_round
                for participant in participants
            ):
                return False
            return all(
                any(_matches_participant(event, participant) for event in relevant)
                for participant in participants
            )
    elif pattern_type == "passthrough_event":

        def match(events, round_table, tick, cfg=pattern_cfg):  # noqa: ARG001 - Shared pattern matcher signature.
            round_index = tick
            round_events = round_table.get(round_index, [])
            expected_fields = cfg.get("where", {})
            required_event_types = set(cfg.get("requires_event_types", []))
            available_event_types = {event["event_type"] for event in round_events}
            return required_event_types.issubset(available_event_types) and any(
                event["event_type"] == cfg["event_type"]
                and cfg.get("domain") in (None, event.get("domain"))
                and _matches_configured_fields(event, expected_fields)
                for event in round_events
            )
    elif pattern_type in {"expression", "obligation"}:
        operator = compile_operator(pattern_cfg)
    else:
        raise ValueError(f"Unsupported pattern type: {pattern_type}")

    compiled = {
        "name": pattern_cfg["name"],
        "domain": pattern_cfg.get("domain"),
        "event_type": pattern_cfg.get("event_type"),
        "where": pattern_cfg.get("where", {}),
        "coord_type": pattern_cfg["coord_type"],
        "priority": pattern_cfg.get("priority", 5),
        "window": pattern_cfg.get("within_ticks", 1),
        "deadline_seconds": pattern_cfg.get("deadline_seconds"),
        "force_process": pattern_cfg.get("force_process", False),
        "action_override": pattern_cfg.get("action"),
        "participants": pattern_cfg.get("participants", []),
        "blocked_action_types": pattern_cfg.get("blocked_action_types", []),
        "block_ttl_seconds": pattern_cfg.get("block_ttl_seconds"),
        "block_scope": pattern_cfg.get("block_scope", "global"),
        "block_id": pattern_cfg.get("block_id", pattern_cfg["name"]),
        "block_domain": pattern_cfg.get("block_domain", pattern_cfg.get("domain")),
        "match": match,
    }
    if operator is not None:
        compiled["operator"] = operator
        compiled["window"] = operator["horizon"]
    return compiled


def get_patterns():
    return [_compile_pattern(p) for p in load_syntony_config()["cep"].get("patterns", [])]
