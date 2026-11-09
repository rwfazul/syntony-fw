"""Execute admitted commands, persist outcomes, update policy state and emit feedback."""

import json
import os
import signal
import subprocess
import time

from ..common import get_logger, get_redis_client, load_syntony_config, log_event
from ..internal_state.control import apply_outcome
from ..policy_profiles import request_activation
from .internal_state import log_action, update_state
from .utils import emit_feedback

logger = get_logger("Executor")


def load_action_handlers():
    """Loads action-type handlers from the Syntony configuration."""
    return load_syntony_config().get("executor", {}).get("actions", {})


def render_template(template, action):
    """Renders command templates with action fields and params placeholders."""
    rendered = str(template)
    replacements = {
        "{{domain}}": str(action.get("domain", "")),
        "{{action_type}}": str(action.get("type", "")),
        "{{policy}}": str(action.get("policy", "")),
        "{{coordination_event_id}}": str(action.get("coordination_event_id", "")),
    }
    for key, value in action.get("params", {}).items():
        replacements[f"{{{{params.{key}}}}}"] = str(value)
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def apply_command_action(action, handler_cfg):
    argv = [render_template(arg, action) for arg in handler_cfg["argv"]]
    if any("{{" in arg for arg in argv):
        raise ValueError("Unresolved command placeholder")
    timeout = float(handler_cfg.get("timeout_seconds", 10))
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
    return {
        "success": not timed_out and process.returncode == 0,
        "status": "timeout" if timed_out else ("ok" if process.returncode == 0 else "error"),
        "mode": "command",
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "returncode": process.returncode,
        "timed_out": timed_out,
        "timeout_seconds": timeout,
    }


def apply_action(action, action_handlers):
    """Routes one action to its configured execution backend."""
    act_type = action["type"]
    domain = action["domain"]
    handler_cfg = action_handlers.get(act_type)
    if not handler_cfg:
        return {"success": False, "status": "unsupported_action", "mode": "command"}
    mode = handler_cfg.get("mode")

    log_event(
        logger,
        "info",
        "action_applying",
        domain=domain,
        action_type=act_type,
        execution_mode=mode,
        policy=action.get("policy"),
    )

    if mode == "command":
        return apply_command_action(action, handler_cfg)
    return {"success": False, "status": "unsupported_mode", "mode": str(mode)}


def feedback_type_for_result(result):
    """Maps one terminal execution result to its feedback subtype."""
    if result.get("status") == "timeout" or result.get("timed_out"):
        return "action_timeout"
    if result.get("success") and result.get("status") == "policy_activated":
        return "policy_activated"
    return "action_done" if result.get("success") else "action_fail"


def feedback_details_for_result(result):
    """Preserves a structured post-condition report emitted by command handlers."""
    stdout = result.get("stdout")
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"stdout": stdout}


def process_executor_command(redis_client, action, action_handlers, executor_domain=None):
    """Execute or recover an action result, apply its state effects and publish feedback."""
    domain = action.get("domain") or executor_domain or "computation"
    identity = action["action_id"]
    result_key = f"executor:result:{identity}"
    existing = redis_client.get(result_key)
    handler = action_handlers.get(action["type"], {})
    activation = handler.get("mode") == "activate_profile"
    saved = json.loads(existing) if existing else {}
    claim = {"state": "running"}
    if activation:
        claim["deadline"] = time.time() + handler.get("timeout_seconds", 10)
    if existing and ("result" in saved or not activation or "deadline" not in saved):
        result = saved.get(
            "result", {"success": False, "status": "execution_uncertain", "mode": "command"}
        )
    elif existing or redis_client.set(result_key, json.dumps(claim), nx=True):
        try:
            if activation:
                result = request_activation(redis_client, action, (saved or claim)["deadline"])
            else:
                result = apply_action(action, action_handlers)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result = {"success": False, "status": "error", "mode": "command", "stderr": str(error)}
        if result["success"] and handler.get("on_success", {}).get("release_exclusivity"):
            report = feedback_details_for_result(result)
            if not isinstance(report, dict) or report.get("verified") is not True:
                result = {**result, "success": False, "status": "unverified_recovery"}
        redis_client.set(result_key, json.dumps({"state": "complete", "result": result}))
    else:
        result = {"success": False, "status": "execution_uncertain", "mode": "command"}

    apply_outcome(redis_client, action, result, handler)

    update_state(redis_client, domain, f"flag_{action['type']}", result["status"])
    log_action(redis_client, domain, action["type"], result["status"])

    feedback_type = feedback_type_for_result(result)
    feedback_payload = {
        "domain": domain,
        "action": action["type"],
        "status": result["status"],
        "policy": action.get("policy"),
        "mode": result.get("mode"),
        "event_time": time.time(),
        "trigger_tick_end": action.get("tick_end"),
        "ticket_id": action.get("ticket_id"),
        "action_id": action.get("action_id"),
        "coordination_event_id": action.get("coordination_event_id"),
        "verification": feedback_details_for_result(result),
    }
    emit_feedback(redis_client, feedback_type, feedback_payload)
    log_event(
        logger,
        "info" if result["success"] else "warning",
        "action_feedback_emitted",
        domain=domain,
        feedback_type=feedback_type,
        action_type=action["type"],
        action_id=action.get("action_id"),
        ticket_id=action.get("ticket_id"),
        status=result["status"],
        execution_mode=result.get("mode"),
        command=result.get("command"),
        returncode=result.get("returncode"),
        timeout_seconds=result.get("timeout_seconds"),
        force_process=action.get("force_process", False),
    )
    return {
        "result": result,
        "feedback_type": feedback_type,
        "feedback_payload": feedback_payload,
    }


def executor_loop():
    """Consumes executor commands and publishes execution feedback.

    Successful and failed executions are both turned into feedback events so
    the other Syntony modules can observe the operational outcome using the
    same stream-oriented model as the rest of the pipeline.
    """
    redis_client = get_redis_client()
    poll_interval = float(os.getenv("POLL_INTERVAL", "0.5"))
    domain = os.getenv("DOMAIN", "computation")
    from ..streams import read_group

    action_handlers = load_action_handlers()

    log_event(
        logger,
        "info",
        "executor_started",
        domain=domain,
        poll_interval=poll_interval,
        configured_actions=list(action_handlers.keys()),
    )

    for eid, e_raw in read_group(redis_client, "executor:commands", "executor", poll_interval):
        action = json.loads(e_raw["data"])
        process_executor_command(redis_client, action, action_handlers, executor_domain=domain)
        redis_client.xack("executor:commands", "executor", eid)


if __name__ == "__main__":
    try:
        executor_loop()
    except KeyboardInterrupt:
        log_event(logger, "info", "executor_stopped")
