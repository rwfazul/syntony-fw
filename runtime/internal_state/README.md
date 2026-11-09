# Internal state

Coordinator and Executor helpers persist Syntony's logical state in Redis.

- `runtime/coordinator/internal_state.py` owns decision records, named action-block leases, release identities and deduplication.
- `runtime/executor/internal_state.py` owns execution status records isolated by domain and action history.
- `runtime/internal_state/control.py` owns declared boolean flags, atomic plan admission, exclusive reservations and idempotent outcome transitions.
- `executor:result:<action_id>` stores started attempts and terminal results.
- `coordinator:processed` stores processed coordination identities.
- Redis consumer groups preserve pending Coordinator/Executor deliveries.

Use one Coordinator and one Executor per instance. Execution records do not provide a coordinated checkpoint of Composer/CEP temporal histories. See the root README for restart, retention and uncertain-result handling.

See [policy state and recovery](../../docs/state-and-policies.md) for configuration, verified release and read interfaces.
