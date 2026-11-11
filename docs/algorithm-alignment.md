# Algorithm-to-implementation alignment

The control pipeline implements the event-time model described in the PhD thesis cited in the project README. This map connects its mechanisms to executable interfaces and behavioral tests.

| Thesis algorithm | Runtime functions | Direct verification |
|---|---|---|
| Watermark admission | `WatermarkManager.observe` | Previous-watermark comparison, inclusive tolerance, monotonicity, explicit bypass |
| Stable control-round closure | `watermark_to_stable_round`, `close_stable_rounds` | Boundary, ordered closure without repetition, exclusion of future context |
| Monitor loop | `collect`, `monitor_loop` | Real command/HTTP/OS acquisition, source timestamp, missing fields |
| Event Composer | `match_rule`, local histories, `composer_loop` | Event-time windows, ordered local sequence, temporal admission |
| CEP loop | `effective_watermark`, `compile_pattern`, `cep_loop` | Required-domain minimum, positive/negative participants, stable rounds |
| Versioned policy activation | `PolicyProfiles.before_round`, `request_activation` | Bidirectional switching, cutover, stale-intent rejection and restart recovery with Redis |
| Coordinator loop | `check_constraints`, `admit_plan`, `map_to_actions`, priority queue | Named blocking/release events, domain/global leases, deadlines, deduplication, boolean guards, atomic plan admission and exclusivity |
| Executor loop | `apply_action`, `process_executor_command`, `apply_outcome` | Real success/failure/timeout, configured flag transitions, idempotent outcomes and verified recovery |

Tests live in `tests/test_runtime.py` and `tests/test_application_and_grid.py`. They call the runtime functions directly rather than relying only on separate simulators. Deployment tests also validate the extended OS instance, per-module images, public interfaces, and generated six-node descriptions.

This is a traceability map, not a formal equivalence proof. Clock-resynchronization execution is delegated to the operating environment. Coordinator/Executor pending delivery is not a coordinated checkpoint of the complete pipeline. No general distributed transaction, rollback, exactly-once side-effect guarantee or queue-source connector is claimed. Docker pipeline and live Grid validation must be reported separately from direct unit/HTTP tests and offline descriptor validation.

## Specification operators

The [operator map](specification-operators.md) covers every operator in the thesis notation, its catalog representation and implementation limits.

`tests/test_specification_operators.py` covers nested Boolean expressions,
bounded historical windows, empty-round absence, obligation deadlines,
maintenance-until conditions, overlapping obligations, snapshot recovery and
profile cancellation. Redis tests in `tests/test_control_state.py` exercise
atomic previous-flag updates and process restart with pending obligations.

## Contract tests

`tests/test_runtime_contracts.py` checks closing-round identity and stable provenance, domain-qualified counts, bounded absolute-time startup, invalid event envelopes, numeric/count/boolean configuration types, frozen per-process configuration, OAR reservation identity, and refusal to overwrite another Ceph FSID. These cases supplement the initial algorithm tests and exercise the implementation's stated contracts.

`tests/test_control_state.py` uses an isolated real Redis server to test competing admissions, plan redelivery, domain flags, complete-plan rejection, policy deadlines, execution failure/timeout, uncertainty retention and verified recovery. Install `redis-server` or set `REDIS_SERVER` to run it. See [state and policies](state-and-policies.md).
