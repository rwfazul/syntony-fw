# Runtime modules

Every executable module is a Python package with an explicit `__all__` interface, a Dockerfile and a requirements file. Start module processes through `python -m runtime <component>`, which validates the complete instance first. The module image defaults select the same component. Build all module images from the repository root so shared configuration/contracts are available.

## Monitor

Public interface: `collect`, `monitor_loop`. Configuration: `domains.<domain>.monitor.sources`; process selection: `--domain`. It polls actual HTTP JSON, command JSON or OS interfaces and publishes metric envelopes. Every source needs a name and explicit metric paths. Timestamps come from `event_time_field` or the local collection clock. A failed source produces a diagnostic, not a substituted value. Adding a protocol requires collection code, validator support, image dependencies and real acquisition tests.

## Event Composer

Public interface: `WatermarkManager`, `match_rule`, `get_ruleset`, `composer_loop`. Configuration: domain `composer.rules`, history TTLs and global `timing`. It manages monotonic domain watermarks, late-event routing, local metric/event histories and cooldown. Operator compilation lives in `rules.py`; evaluation lives in `composer.py`. Published observations retain source provenance and temporal admission context.

## CEP

Public interface: `compile_pattern`, `effective_watermark`, `close_stable_rounds`, `cep_loop`. Configuration: `cep.patterns`, `watermark_domains`, control-round length and retention. It correlates events only when the minimum required-domain watermark closes a stable round, with a separately configured exception for force-enabled administrative patterns. Its input is observations plus watermark progress, not raw system metrics. The isolated deployment needs real upstream producers.

## Coordinator

Public interface: `map_to_actions`, `check_constraints`, `coordinator_loop`. Configuration: `coordinator.policy_mappings`, `policy_constraints`, deduplication and event-level block metadata. It orders available intentions by priority, checks deadlines/dedupe/leases and declared state guards, maps policies to target-domain actions, then atomically reserves exclusivity, stores dispatch flags and enqueues the plan. Consumer-group pending delivery is persisted. Only one Coordinator per instance is supported; this is not a distributed arbitration cluster.

## Executor

Public interface: `apply_action`, `process_executor_command`, `executor_loop`. Configuration: `executor.actions`, each with `mode: command`, an `argv` array and timeout. Handlers also declare outcome-specific flag effects. It performs actual operations, records results, applies each transition once and emits `E_fed` with terminal status and verification output. The adapter must check its target system's post-condition. Missing handlers fail. Redelivery reuses an existing result; interrupted attempts remain uncertain rather than being retried blindly. Only one Executor per instance is supported.

## Internal state

`get_coordinator_state` and `get_execution_state` expose their respective records. `get_flag` and `get_flags` expose declared boolean policy state; `control.py` owns atomic admission, exclusive plans and outcome transitions. The implementation owners remain the Coordinator/Executor helpers. Redis also holds pending commands, leases, deduplication and execution results. This logical layer does not create a sixth daemon or make in-memory temporal context a coordinated checkpoint.
