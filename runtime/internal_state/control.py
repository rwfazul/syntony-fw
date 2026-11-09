"""Persist policy flags, reserve exclusive action plans and apply outcome transitions."""

import hashlib
import json
import time

from ..common import load_syntony_config
from ..policy_profiles import ACTIVE_PROFILE, profile_matches

FLAGS_KEY = "control:flags"
PLANS_KEY = "control:plans"
EXCLUSIVE_KEY = "control:exclusive"
TRANSITIONS_KEY = "control:outcomes"


def flag_key(domain, name):
    return json.dumps([domain, name], separators=(",", ":"))


def get_flag(client, domain, name, config=None):
    config = config or load_syntony_config()
    default = config.get("state", {}).get("flags", {}).get(domain, {})
    if name not in default:
        raise ValueError(f"Undeclared state flag: {domain}/{name}")
    value = client.hget(FLAGS_KEY, flag_key(domain, name))
    result = json.loads(value) if value is not None else default[name]
    if type(result) is not bool:
        raise ValueError(f"Invalid persisted state flag: {domain}/{name}")
    return result


def get_flags(client, config=None):
    config = config or load_syntony_config()
    return {
        domain: {name: get_flag(client, domain, name, config) for name in flags}
        for domain, flags in config.get("state", {}).get("flags", {}).items()
    }


def guards_match(client, required, config=None):
    return all(
        get_flag(client, domain, name, config) == expected
        for domain, flags in required.items()
        for name, expected in flags.items()
    )


def write_flags(client, values):
    for domain, flags in values.items():
        for name, value in flags.items():
            client.hset(FLAGS_KEY, flag_key(domain, name), json.dumps(value))


def admit_plan(client, event, actions, config=None):
    """Atomically check guards, reserve exclusivity and enqueue a complete plan."""
    import redis

    from ..coordinator.internal_state import (
        ACTION_BLOCK_KEY,
        DEDUPE_KEY,
        check_constraints,
        register_dedupe,
    )

    config = config or load_syntony_config()
    constraints = (
        config.get("coordinator", {}).get("policy_constraints", {}).get(event["policy"], {})
    )
    if not actions:
        raise ValueError("An action plan must contain at least one action")
    ticket = actions[0]["ticket_id"]
    if any(a.get("ticket_id") != ticket or not a.get("action_id") for a in actions):
        raise ValueError("Every command must identify the same action plan")
    if len({a["action_id"] for a in actions}) != len(actions):
        raise ValueError("Action IDs must be unique within a plan")
    fingerprint = hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()
    exclusive = constraints.get("exclusivity_class")
    for _ in range(10):
        with client.pipeline() as pipe:
            try:
                pipe.watch(FLAGS_KEY, PLANS_KEY, EXCLUSIVE_KEY, DEDUPE_KEY, ACTION_BLOCK_KEY)
                if config.get("cep", {}).get("policy_profiles") or "policy_version" in event:
                    pipe.watch(ACTIVE_PROFILE)
                existing = pipe.hget(PLANS_KEY, ticket)
                if existing:
                    if json.loads(existing)["fingerprint"] != fingerprint:
                        raise ValueError("Action plan identity was reused with different commands")
                    return True, "already_dispatched"
                if not profile_matches(pipe, event):
                    return False, "stale_policy_profile"
                if not guards_match(pipe, constraints.get("required_flags", {}), config):
                    return False, "state_guard"
                if exclusive and pipe.hget(EXCLUSIVE_KEY, exclusive):
                    return False, "exclusivity"
                if any(
                    not check_constraints(event, pipe, action["type"], action["domain"], config)
                    for action in actions
                ):
                    return False, "policy_constraint"
                plan = dict(
                    ticket_id=ticket,
                    policy=event["policy"],
                    fingerprint=fingerprint,
                    actions=[a["action_id"] for a in actions],
                    completed={},
                    exclusivity_class=exclusive,
                    state="dispatched",
                    created_at=time.time(),
                )
                pipe.multi()
                write_flags(pipe, constraints.get("set_flags_on_dispatch", {}))
                if exclusive:
                    pipe.hset(EXCLUSIVE_KEY, exclusive, ticket)
                pipe.hset(PLANS_KEY, ticket, json.dumps(plan))
                for action in actions:
                    pipe.xadd("executor:commands", {"data": json.dumps(action)})
                register_dedupe(event, pipe, config)
                if event.get("coordination_event_id"):
                    pipe.sadd(
                        "coordinator:processed",
                        f"{event['coordination_event_id']}:{event['policy']}",
                    )
                pipe.execute()
                return True, "dispatched"
            except redis.WatchError:
                continue
    raise RuntimeError("State changed repeatedly while admitting an action plan")


def apply_outcome(client, action, result, handler, config=None):
    """Apply each action's configured flag effects once and release completed plans."""
    uncertain = result.get("timed_out") or result["status"] in {"timeout", "execution_uncertain"}
    transition = (
        "on_uncertain"
        if result["status"] == "execution_uncertain"
        else ("on_timeout" if uncertain else ("on_success" if result["success"] else "on_failure"))
    )
    effects = handler.get(
        transition, handler.get("on_timeout", {}) if transition == "on_uncertain" else {}
    )
    if effects.get("release_exclusivity"):
        try:
            report = json.loads(result.get("stdout", ""))
        except (ValueError, TypeError):
            report = None
        if (
            not result["success"]
            or not isinstance(report, dict)
            or report.get("verified") is not True
        ):
            raise ValueError("Releasing uncertain plans requires a verified successful recovery")
    ticket = action.get("ticket_id")
    if not ticket and not effects:
        return
    import redis

    config = config or load_syntony_config()
    identity = action["action_id"]
    for _ in range(10):
        with client.pipeline() as pipe:
            try:
                pipe.watch(FLAGS_KEY, PLANS_KEY, EXCLUSIVE_KEY, TRANSITIONS_KEY)
                if pipe.hget(TRANSITIONS_KEY, identity):
                    return
                raw = pipe.hget(PLANS_KEY, ticket) if ticket else None
                plan = json.loads(raw) if raw else None
                if ticket and (not plan or identity not in plan["actions"]):
                    raise ValueError("Execution outcome has no admitted action plan")
                releases = effects.get("release_exclusivity", [])
                owners = {name: pipe.hget(EXCLUSIVE_KEY, name) for name in releases}
                resolved = {}
                for name, owner in owners.items():
                    if owner:
                        prior = pipe.hget(PLANS_KEY, owner)
                        if not prior:
                            raise ValueError("Exclusive reservation has no owning plan record")
                        record = json.loads(prior)
                        if record["state"] != "uncertain":
                            raise ValueError("Recovery cannot release an active nonterminal plan")
                        record.update(state="resolved", resolved_by=identity)
                        resolved[owner] = record
                if plan:
                    plan["completed"][identity] = result["status"]
                    finished = len(plan["completed"]) == len(plan["actions"])
                    held = any(
                        value in {"timeout", "execution_uncertain"}
                        for value in plan["completed"].values()
                    )
                    plan["state"] = ("uncertain" if held else "complete") if finished else "running"
                    own_class = plan.get("exclusivity_class")
                    if finished and not held and own_class:
                        owners[own_class] = pipe.hget(EXCLUSIVE_KEY, own_class)
                        if owners[own_class] == ticket:
                            releases = [*releases, own_class]
                updates = {
                    domain: dict(flags) for domain, flags in effects.get("set_flags", {}).items()
                }
                for domain, names in effects.get("clear_flags", {}).items():
                    updates.setdefault(domain, {}).update({name: False for name in names})
                if plan:
                    if uncertain:
                        retained = plan.setdefault("uncertain_flags", {})
                        for domain, flags in updates.items():
                            retained.setdefault(domain, {}).update(flags)
                    for domain, flags in plan.get("uncertain_flags", {}).items():
                        updates.setdefault(domain, {}).update(flags)
                pipe.multi()
                write_flags(pipe, updates)
                for name in releases:
                    if owners.get(name):
                        pipe.hdel(EXCLUSIVE_KEY, name)
                for owner, record in resolved.items():
                    pipe.hset(PLANS_KEY, owner, json.dumps(record))
                if plan:
                    pipe.hset(PLANS_KEY, ticket, json.dumps(plan))
                pipe.hset(
                    TRANSITIONS_KEY,
                    identity,
                    json.dumps(
                        dict(status=result["status"], transition=transition, at=time.time())
                    ),
                )
                pipe.execute()
                return
            except redis.WatchError:
                continue
    raise RuntimeError("State changed repeatedly while applying execution feedback")
