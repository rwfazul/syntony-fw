"""Store coordination decisions, deduplication markers and bounded action-block leases."""

import json
import time

from ..common import load_syntony_config

STATE_KEY = "internal_state"
DEDUPE_KEY = "internal_state_dedupe"
ACTION_BLOCK_KEY = "internal_state_action_blocks"
ACTION_RELEASE_KEY = "internal_state_action_releases"


def update_state(redis_client, event_id, status, action=None, tick=None):
    """Record the Coordinator decision in internal state."""
    record = {
        "timestamp": time.time(),
        "event_id": event_id,
        "status": status,
        "action": json.dumps(action) if action else None,
        "tick": tick,
    }
    redis_client.hset(STATE_KEY, event_id, json.dumps(record))


def get_state(redis_client):
    """Read all Coordinator decision records for inspection."""
    raw = redis_client.hgetall(STATE_KEY)
    return {k: json.loads(v) for k, v in raw.items()}


def _get_dedupe_rules(config=None):
    return (config or load_syntony_config()).get("coordinator", {}).get("dedupe_rules", [])


def _event_matches_dedupe_rule(event, rule):
    policy_match = rule.get("policy") in (None, event.get("policy"))
    coord_match = rule.get("coord_type") in (None, event.get("coord_type"))
    return policy_match and coord_match


def _build_dedupe_key(event, rule):
    scope = rule.get("scope", "domain")
    if scope == "domain":
        return f"{event.get('policy')}:{event.get('domain')}"
    return f"{event.get('policy')}:{event.get('coord_type')}"


def _dedupe_blocks_event(event, redis_client, config=None):
    if event.get("force_process"):
        return False

    rules = _get_dedupe_rules(config)
    state = redis_client.hgetall(DEDUPE_KEY)
    now = time.time()
    for rule in rules:
        if not _event_matches_dedupe_rule(event, rule):
            continue

        dedupe_key = _build_dedupe_key(event, rule)
        raw = state.get(dedupe_key)
        if not raw:
            continue
        record = json.loads(raw)
        if now - record["timestamp"] < rule.get("window_seconds", 0):
            return True
    return False


def register_dedupe(event, redis_client, config=None):
    """Persists dedupe markers for future coordination events."""
    rules = _get_dedupe_rules(config)
    now = time.time()
    for rule in rules:
        if not _event_matches_dedupe_rule(event, rule):
            continue
        dedupe_key = _build_dedupe_key(event, rule)
        redis_client.hset(
            DEDUPE_KEY,
            dedupe_key,
            json.dumps(
                {
                    "timestamp": now,
                    "policy": event.get("policy"),
                    "domain": event.get("domain"),
                    "coord_type": event.get("coord_type"),
                }
            ),
        )


def register_action_block(event, redis_client):
    """Registers a bounded policy lease that inhibits selected action types."""
    blocked_action_types = sorted(set(event.get("blocked_action_types", [])))
    if not blocked_action_types:
        return None
    now = time.time()
    ttl_seconds = float(event.get("block_ttl_seconds") or 0.0)
    block_id = event.get("block_id") or event["policy"]
    domain = event.get("block_domain") or event["domain"]
    issued_at = event.get("timestamp", now)
    key = f"{block_id}:{domain}"
    released = redis_client.hget(ACTION_RELEASE_KEY, key)
    previous = redis_client.hget(ACTION_BLOCK_KEY, key)
    if released and json.loads(released)["issued_at"] >= issued_at:
        return None
    if previous and json.loads(previous).get("issued_at", 0) > issued_at:
        return None
    record = {
        "block_id": block_id,
        "issued_at": issued_at,
        "policy": event.get("policy"),
        "domain": domain,
        "scope": event.get("block_scope", "global"),
        "blocked_action_types": blocked_action_types,
        "created_at": now,
        "expires_at": now + ttl_seconds if ttl_seconds > 0 else None,
        "coordination_event_id": event.get("coordination_event_id"),
    }
    redis_client.hset(ACTION_BLOCK_KEY, key, json.dumps(record))
    return record


def release_action_block(event, redis_client):
    """Release one named guard and record its event identity in one transaction."""
    import redis

    block_id, domain = event["block_id"], event["block_domain"]
    key = f"{block_id}:{domain}"
    identity = f"{event['coordination_event_id']}:{event['policy']}"
    issued_at = event["timestamp"]
    for _ in range(10):
        with redis_client.pipeline() as pipe:
            try:
                pipe.watch(ACTION_BLOCK_KEY, ACTION_RELEASE_KEY, "coordinator:processed")
                if pipe.sismember("coordinator:processed", identity):
                    return False
                raw = pipe.hget(ACTION_BLOCK_KEY, key)
                record = json.loads(raw) if raw else None
                previous = pipe.hget(ACTION_RELEASE_KEY, key)
                latest_release = json.loads(previous)["issued_at"] if previous else float("-inf")
                remove = record is not None and record.get("issued_at", 0) <= issued_at
                pipe.multi()
                if remove:
                    pipe.hdel(ACTION_BLOCK_KEY, key)
                if issued_at > latest_release:
                    pipe.hset(
                        ACTION_RELEASE_KEY,
                        key,
                        json.dumps(
                            {
                                "issued_at": issued_at,
                                "coordination_event_id": event["coordination_event_id"],
                            }
                        ),
                    )
                pipe.sadd("coordinator:processed", identity)
                pipe.execute()
                return remove
            except redis.WatchError:
                continue
    raise RuntimeError("Guard state changed repeatedly during release")


def get_active_action_blocks(redis_client, event, action_type):
    """Returns non-expired policy blocks applicable to an action candidate."""
    if not action_type or event.get("force_process"):
        return []
    now = time.time()
    matches = []
    for key, raw in redis_client.hgetall(ACTION_BLOCK_KEY).items():
        record = json.loads(raw)
        expires_at = record.get("expires_at")
        if expires_at is not None and now >= expires_at:
            redis_client.hdel(ACTION_BLOCK_KEY, key)
            continue
        if action_type not in record.get("blocked_action_types", []):
            continue
        if record.get("scope") == "domain" and record.get("domain") != event.get("domain"):
            continue
        matches.append(record)
    return matches


def check_constraints(event, redis_client, action_type=None, action_domain=None, config=None):
    """Enforces deadlines, configured dedupe windows and active action leases."""
    if event.get("deadline") and not event.get("force_process") and time.time() > event["deadline"]:
        return False

    config = config or load_syntony_config()
    constraints = (
        config.get("coordinator", {}).get("policy_constraints", {}).get(event["policy"], {})
    )
    lifetime = constraints.get("deadline_seconds")
    if lifetime is not None and time.time() > event.get("timestamp", 0) + lifetime:
        return False

    from ..internal_state.control import guards_match

    if not guards_match(redis_client, constraints.get("required_flags", {}), config):
        return False

    if _dedupe_blocks_event(event, redis_client, config):
        return False

    block_context = {**event, "domain": action_domain or event["domain"]}
    if get_active_action_blocks(redis_client, block_context, action_type):
        return False

    return True
