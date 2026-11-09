# Policy state and execution recovery

The Coordinator decides whether a complete action plan may start. The Executor records what happened and applies the configured state transition. Both use the same logical state layer in Redis; no additional state service is required.

## Declare flags and admission rules

Declare boolean defaults under `state.flags`, qualified by a configured domain. Defaults apply only when a value has not been stored; restarting a process does not reset stored state.

This excerpt illustrates the restart policy shipped in `specifications/policies/service_resilience.json`:

```json
{
  "state": {
    "flags": {
      "application_service": {
        "service_reconfiguration_running": false,
        "service_recovery_needed": false,
        "service_status_unknown": false
      }
    }
  },
  "coordinator": {
    "policy_constraints": {
      "restart_confirmed_local_failure": {
        "required_flags": {
          "application_service": {
            "service_reconfiguration_running": false,
            "service_recovery_needed": false,
            "service_status_unknown": false
          }
        },
        "set_flags_on_dispatch": {
          "application_service": {"service_reconfiguration_running": true}
        },
        "exclusivity_class": "service_reconfiguration",
        "deadline_seconds": 10
      }
    }
  }
}
```

All `required_flags` conditions must hold. A policy may read flags from several domains. The `exclusivity_class` is an instance-wide identifier: policies using the same name cannot hold concurrent plans, even when their target domains differ. Use distinct classes for independent operations.

Admission checks state guards, exclusivity, deadlines, deduplication and applicable `E_block` leases. A Redis transaction then stores the dispatch flags and plan, reserves the class, queues every command and records deduplication. A rejected plan queues no commands and changes no flags. Replaying an already-admitted plan does not enqueue it again. Reusing its ticket with different commands is rejected.

Each plan has a `ticket_id`; each command has a distinct `action_id`. Preserve these identifiers when adapting an external executor. Commands within a plan are queued in declared order. Admission of the whole plan does not provide rollback or conditional cancellation of later commands after an earlier failure.

`deadline_seconds` in a policy is measured from the coordination event's `timestamp`. A CEP pattern can also declare its own `deadline_seconds`, which becomes an absolute event deadline. When both apply, admission must satisfy both. The correlation window `within_ticks` is independent of the admission deadline. Deadlines govern dispatch, while handler `timeout_seconds` governs execution.

State guards and exclusive reservations remain mandatory for `force_process` events. Force-processing can bypass temporal admission/deduplication and ordinary blocking leases; it does not authorize concurrent reconfiguration or clear uncertain state.

## Configure outcome transitions

An action handler can declare these effects in `on_success`, `on_failure`, `on_timeout` and `on_uncertain`:

| Field | Value | Effect |
|---|---|---|
| `set_flags` | Domain → flag → boolean | Store the supplied values |
| `clear_flags` | Domain → list of names | Set those flags to `false` |
| `release_exclusivity` | List of declared class names | Resolve held uncertain plans after verified recovery; allowed only in `on_success` |

The default restart handler uses:

```json
{
  "on_success": {
    "clear_flags": {
      "application_service": [
        "service_reconfiguration_running",
        "service_recovery_needed",
        "service_status_unknown"
      ]
    }
  },
  "on_failure": {
    "clear_flags": {
      "application_service": ["service_reconfiguration_running"]
    },
    "set_flags": {
      "application_service": {"service_recovery_needed": true}
    }
  },
  "on_timeout": {
    "set_flags": {
      "application_service": {
        "service_reconfiguration_running": true,
        "service_status_unknown": true
      }
    }
  }
}
```

These are handler fields alongside `mode`, `argv` and `timeout_seconds`. `on_uncertain` handles an interrupted attempt whose outcome was not saved; if omitted, it uses `on_timeout`. A known nonzero exit or unsupported action uses `on_failure`. The terminal result is saved before applying effects. Each action's effects are applied once, so result redelivery cannot reset flags changed by a later recovery.

A class is released automatically after every command in its plan has a known terminal outcome. If any command timed out or has an uncertain outcome, the class remains held. Flag effects from that uncertainty transition remain in force for the plan, so a later successful command in the same plan cannot clear its uncertainty guards. A known failure releases the class but the default `service_recovery_needed` guard still prevents normal policies from restarting. Choose effects for each adapter's operational semantics; a process timeout cannot establish whether a remote operation finished.

## Verify recovery before releasing uncertainty

The example catalog provides two recovery policies: `verify_uncertain_local_recovery` and `verify_failed_local_recovery`. They correlate local worker availability with dependency availability, require the corresponding state flag, and request `VERIFY_LOCAL_SERVICE`.

That handler reads the actual supervisor state and probes the service. It changes no service configuration. Its JSON report must contain `"verified": true`, and the command must succeed, before these effects apply:

```json
{
  "on_success": {
    "clear_flags": {
      "application_service": [
        "service_reconfiguration_running",
        "service_recovery_needed",
        "service_status_unknown"
      ]
    },
    "release_exclusivity": ["service_reconfiguration"]
  }
}
```

A zero exit code without that explicit report becomes `unverified_recovery` and emits failure feedback. Recovery cannot release a plan that is still actively executing. The original uncertain plan is marked `resolved` with the recovery action identity. Read-only verification policies do not reserve the class they need to recover; otherwise that reservation would prevent their own admission.

For another platform, implement verification that establishes its actual post-condition and resolves any outstanding remote operation. Never clear a guard solely because a waiting period elapsed. If recovery requires operator action, retain the guards until that action has been verified.

## Inspect state and operate an instance

```python
from runtime.common import get_redis_client
from runtime.internal_state import get_flags

flags = get_flags(get_redis_client())
```

`control:flags` stores domain-qualified values, `control:plans` stores admitted plans and completion status, `control:exclusive` maps classes to owning tickets, and `control:outcomes` records applied transitions. Coordinator decision records distinguish `state_guard`, `exclusivity`, `policy_constraint`, `dispatched` and `already_dispatched`.

Use one Coordinator and one Executor for each instance. Keep Redis state and configuration consistent across restarts. Plan/result records have no automatic expiry: define backup and retention procedures that preserve idempotency and unresolved reservations. The CEP checkpoints its Round Table and input cursors separately. The Composer retains its working history in memory.

`tests/test_control_state.py` runs competing admissions, real execution timeouts, recovery verification and idempotent replay against an isolated Redis server. It starts its own temporary database and never connects to a configured deployment.
