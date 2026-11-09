# Coordination event specifications

Event packages define CEP patterns over observed events and execution feedback. Patterns select participating domains, correlation windows, matching fields, priority and the coordination event type.

The thesis notation is used throughout: `E_trg` requests an adaptation candidate, `E_sync` requests a joint adaptation, `E_block` activates a named action guard and `E_unblock` releases it. Executor feedback is `E_fed`.

`service_resilience.json` correlates application/dependency observations. `host_maintenance_guard.json` blocks restarts during high load and explicitly releases that guard when load returns to the normal range.

See [event contracts](../../docs/event-contracts.md) and [catalog extension](../../docs/extending-the-catalog.md) for fields and examples.
