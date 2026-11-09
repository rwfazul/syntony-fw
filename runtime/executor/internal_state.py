"""Domain-local execution state, separate from Coordinator decisions."""

import json
import time

STATE_KEY = "internal_state_executor"


def get_state(redis_client):
    return {domain: json.loads(raw) for domain, raw in redis_client.hgetall(STATE_KEY).items()}


def update_state(redis_client, domain, flag, value):
    raw = redis_client.hget(STATE_KEY, domain)
    state = json.loads(raw) if raw else {"flags": {}}
    state["flags"][flag] = value
    redis_client.hset(STATE_KEY, domain, json.dumps(state))


def log_action(redis_client, domain, action_name, status):
    redis_client.rpush(
        f"history:{domain}",
        json.dumps(dict(action=action_name, status=status, timestamp=time.time())),
    )
