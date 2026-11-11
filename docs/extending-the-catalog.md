# Extending the Specification Catalog

The catalog is a set of executable JSON specifications consumed by the existing runtime, not a directory of documentation-only templates. Extend the smallest layer that expresses your change:

| Change | Files to modify |
|---|---|
| Bind a different endpoint | Declared parameters/environment, instance configuration |
| Add a measured quantity using an existing source protocol | Domain source mappings |
| Detect a new local condition | Domain `composer.rules` |
| Correlate existing observations | Event pattern package |
| Change an action or its parameters | Policy mapping and handler package |
| Add a domain | Domain package, instance includes, required domains, Monitor/Composer deployment |
| Add a protocol/operator | Runtime implementation, validation, dependencies and direct tests |

## 1. Select reusable packages

`config/syntony_config.json` includes domain, event and policy packages. Paths resolve relative to the file that declares them. Includes may contain other includes; cycles are rejected. Objects merge, lists concatenate, and conflicting scalar values fail rather than silently overriding one another.

To specialize a threshold, copy the relevant package, edit the numeric threshold, and include the specialized package instead of the original. Including both definitions with the same rule/pattern name is an error. Parameter substitutions such as `${GATEWAY_URL}` are strings and require declaration in the root `parameters` object; they are suitable for endpoints/argv, not a substitute for typed numeric thresholds.

## 2. Add a real domain

The shipped `specifications/domains/operating_system.json` contains a real OS source and a local threshold event. Its core structure is:

```json
{
  "domains": {
    "operating_system": {
      "monitor": {
        "sources": [{
          "name": "local_os_load",
          "type": "system",
          "event_time_field": "timestamp",
          "parser": {"type": "json", "metrics": [
            {"metric": "load_avg_1m", "path": "load_avg_1m"}
          ]}
        }]
      },
      "composer": {
        "rules": [{
          "name": "host_load_high", "type": "threshold",
          "metric": "load_avg_1m", "operator": "gt", "value": 4,
          "cooldown_seconds": 5
        }]
      }
    }
  }
}
```

This is a load average from the kernel available to the Monitor, not CPU percentage or guaranteed container-local utilization. The threshold of 4 is illustrative policy configuration; calibrate it for the actual hardware and workload. On Linux, the source can also expose `memory_available_bytes`; the memory field is unavailable on platforms without `/proc/meminfo`.

A ready-to-validate extended instance is supplied:

```bash
python -m runtime validate --config config/service-resilience-host-guard.json
```

It selects the normal HTTP application plus the OS package and a guard pattern. It appends `operating_system` to the required watermark domains.

## 3. Turn local observations into coordination events

`specifications/events/host_maintenance_guard.json` contains a complete optional example:

```json
{
  "cep": {"patterns": [{
    "name": "guard_restart_during_host_pressure",
    "block_id": "host_pressure",
    "type": "passthrough_event",
    "domain": "operating_system",
    "event_type": "host_load_high",
    "coord_type": "E_block",
    "blocked_action_types": ["RESTART_LOCAL_SERVICE"],
    "block_scope": "global",
    "block_ttl_seconds": 5,
    "priority": 0
  }]}
}
```

The Coordinator records a bounded lease that blocks the existing restart action while the guard applies. The shipped package also defines `host_load_normal` and an `E_unblock` pattern sharing `block_id: host_pressure`. A normal-load observation releases that named guard. See [event contracts](event-contracts.md#blocking-and-release) for identity and replay semantics. This pattern does not require a new Executor handler: it configures arbitration. Repeated observations can renew the lease. Enabling the example can intentionally delay or suppress the restart demonstration; choose its threshold and run the recovery demo on the default configuration when checking the unguarded path.

Optional `expression` and `obligation` patterns add nested Boolean and bounded temporal rules. See the [operator syntax and runnable example](specification-operators.md#expression-syntax).

For the thesis notation and the scope of each logical/temporal operator, see the [specification operator map](specification-operators.md). Use `same_round_all` for named events in one stable round; `same_tick_all` is its compatibility alias.

For multiple domains use `participants_all`, with explicit `domain`/`event_type` pairs and `within_rounds`. `forbidden_participants` uses the same event-time window to veto a match. `where` checks equality on event fields. The largest `within_rounds` must fit `cep.round.retention_rounds`. Required domains must actually have active observation/watermark producers; adding a name to configuration alone does not start its services.

## 4. Start every new producer

An executable Compose override is supplied for the OS instance:

```bash
docker compose -f deployment/examples/local/compose.json \
  -f deployment/examples/local/host-guard.compose.json config --quiet
docker compose -p syntony-host-guard -f deployment/examples/local/compose.json \
  -f deployment/examples/local/host-guard.compose.json up --build -d --wait
```

The override starts `monitor_operating_system` and `composer_operating_system` using their module Dockerfiles and changes the configuration path for all runtime services. The separate Compose project gives this configuration its own Redis volume and CEP checkpoint. Stop the default stack first if it uses the same gateway port. Adding a required domain without its Composer stalls global watermark progress by design.

## 5. Configure concrete actions

Normal triggering/synchronizing patterns require `coordinator.policy_mappings[pattern_name]`. Each command declares its target domain, action type and params. `executor.actions[action_type]` declares the real executable binding:

```json
{
  "coordinator": {"policy_mappings": {
    "restart_confirmed_local_failure": [{
      "domain": "application_service", "type": "RESTART_LOCAL_SERVICE", "params": {}
    }]
  }},
  "executor": {"actions": {
    "RESTART_LOCAL_SERVICE": {
      "mode": "command",
      "argv": ["python", "deployment/examples/local/control_action.py",
               "--gateway", "${GATEWAY_URL}", "--action", "RESTART_LOCAL_SERVICE"],
      "timeout_seconds": 6
    }
  }}
}
```

The existing action adapter checks the actual post-condition and exits nonzero if it cannot verify the restart. Implement your platform-specific adapter with the same convention. JSON stdout is preserved in feedback. No shell expansion occurs: each `argv` element is passed separately. Supported substitutions are `{{domain}}`, `{{action_type}}`, `{{policy}}`, `{{coordination_event_id}}` and `{{params.NAME}}`. Do not replace a real operation with an unconditional echo/zero exit status.

Mappings may contain multiple commands in declared order, but this does not provide transactional execution, rollback or dependencies between separate Executors. Keep handlers idempotent and use the action identity supplied by Syntony when your target API supports it.

## 6. Configure policy state and recovery

Use `state.flags` to declare domain-qualified booleans and `coordinator.policy_constraints` to bind admission guards, dispatch effects, exclusivity classes and deadlines to a pattern name. Configure `on_success`, `on_failure`, `on_timeout` and optionally `on_uncertain` on action handlers. The [policy state guide](state-and-policies.md) explains the complete contract and the shipped verified recovery policies.

Use the [monitor examples](../deployment/examples/monitors/README.md) to exercise each supported acquisition protocol against actual host or service metrics before connecting a new policy.

## 7. Add a new source protocol or rule operator

For a new acquisition protocol, implement it in `runtime/monitor/monitor.py:collect`, extend startup validation in `runtime/configuration.py`, add dependency requirements to the Monitor image, and test actual acquisition/failure behavior. The current supported types are `http_json`, `command` and `system`; `queue` is rejected rather than advertised as an implemented consumer.

For a new Composer operator, extend compilation in `runtime/composer/rules.py` and evaluation in `runtime/composer/composer.py:match_rule`. Add matching startup checks. For a CEP operator, extend `runtime/cep/patterns.py` and test stable-round and domain-boundary behavior. Export a public interface through the package `__init__.py` only when downstream integrations need it.

Keep histories long enough for local windows. Existing local operators are `threshold`, `all_metrics`, `any_metrics`, `metric_window`, `event_combo_all`, `event_sequence` and `external_check`. Comparison operators are `gt`, `gte`, `lt`, `lte`, `eq` and `neq`. Time windows/cooldowns use event time, not a count of polling cycles.

## 8. Validate the resulting instance

```bash
make validate hygiene test compose-check
python -m runtime validate --config config/service-resilience-host-guard.json
# Explicitly, with Docker available and a fresh default instance:
make integration
```

Validate positive and negative participants, unknown references, missing sources, failure/timeout feedback and handler post-conditions. Use storage doubles only in tests. Validate actual Redis delivery and infrastructure separately before claiming a new integration works end to end. Catalog packages are assembled on startup. For runtime switching between predefined, versioned CEP policy profiles, see [meta-reconfiguration](meta-reconfiguration.md).

## Repository checks and distributable assets

`make hygiene` checks required module assets, Python syntax, Markdown heading structure and relative links without contacting external services. It is also run by CI. These checks complement behavioral tests; they do not infer conceptual correctness.

Use `python scripts/bundle.py --output dist/syntony-fw.tar.gz` for a full deployment source bundle, including the catalog and guides. The Python wheel installs the runtime/CLI and license; supply an external instance configuration when using it outside the checkout. The source distribution includes configuration, catalog, deployment assets and documentation through `MANIFEST.in`.

## Compose a filesystem guard

The [filesystem guard instance](../config/service-resilience-filesystem-guard.json) combines the default recovery instance, a new domain package and a guard pattern package. The domain package can also run in the [observation-only example](../config/examples/composer-filesystem.json).

| Package | Contribution |
|---|---|
| [Filesystem capacity domain](../specifications/domains/filesystem_capacity.json) | Actual filesystem capacity, kernel load and local rules |
| [Filesystem restart guard](../specifications/events/filesystem_restart_guard.json) | `E_block` and `E_unblock` for the existing `RESTART_LOCAL_SERVICE` action |
| [Service resilience policies](../specifications/policies/service_resilience.json) | Existing restart mapping and handler reused by the instance |

Three samples at or above 90% usage within ten seconds produce `filesystem_pressure`. The CEP turns that observation into a bounded restart guard. A stable round with a headroom observation at or below 85% and no new pressure observation releases the guard. Earlier high-utilization samples remain relevant until their window expires. Adjust the thresholds and windows to the target filesystem.

```bash
python -m runtime validate --config config/service-resilience-filesystem-guard.json
docker compose -p syntony-filesystem -f deployment/examples/local/compose.json \
  -f deployment/examples/local/filesystem-guard.compose.json up --build -d --wait
```

The Monitor measures the filesystem containing `CAPACITY_PATH` inside its container. The default is `/app`. To measure a different filesystem, mount it read-only and point `CAPACITY_PATH` to the mounted path. The new Monitor and Composer publish observations and watermark progress for `filesystem_capacity`, which participates in the CEP's minimum watermark.
