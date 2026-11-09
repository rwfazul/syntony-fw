# Policy specifications

Policy packages map CEP pattern names to executable action plans. They also declare boolean state defaults, admission guards, dispatch effects, exclusivity classes, deadlines, deduplication windows and action handlers.

`service_resilience.json` binds the supplied service recovery instantiation to command adapters that inspect actual post-conditions. Adapt these bindings and state transitions for another managed system.

See [policy state and recovery](../../docs/state-and-policies.md) for the complete configuration contract and [catalog extension](../../docs/extending-the-catalog.md) for new handlers.
