# Specification Catalog

Syntony correlates observations from multiple domains and applies policies to coordinate adaptive reconfiguration. The Specification Catalog defines what an instance observes, how it interprets observations and which actions it can request. The runtime evaluates these declarations and calls the configured adapters.

## Specification layers

- **Domains** define metric sources and Composer rules that turn local conditions into observed events.
- **Events** define CEP patterns that combine observations into coordination events.
- **Policies** define action mappings, execution handlers and admission constraints.

A configured control path connects these identifiers:

```text
source metric name
  → Composer rule's metric/conditions
  → observed event type (the rule name)
  → CEP pattern's event_type/participants
  → coordination policy (the pattern name)
  → coordinator.policy_mappings[pattern name]
  → executor.actions[action type]
```

## Select packages for an instance

The instance configuration loads packages through `includes` and sets parameters such as timing and endpoint bindings. Each active domain needs a Monitor and a Composer. Validate the assembled catalog with `python -m runtime validate --config <instance.json>`. [Instance configuration guide](../config/README.md)

## Supplied examples

The default configuration selects HTTP service recovery across an application domain and its external dependency. [Default example configuration](../config/syntony_config.json)

| Package | Role | Selection |
|---|---|---|
| `domains/application_service.json` | HTTP application metrics and local degradation, availability and fallback observations | Default instance |
| `domains/external_dependency.json` | Dependency health metrics and local availability/recovery observations | Default instance |
| `domains/operating_system.json` | Actual OS load and high/normal-load observations | Optional OS instance |
| `events/service_resilience.json` | Cross-domain patterns that identify dependency failure, local-process failure and recovery | Default instance |
| `events/host_maintenance_guard.json` | A named restart guard with blocking, explicit release and TTL expiry | Optional OS instance |
| `policies/service_resilience.json` | Pattern-to-action mappings, command handlers and deduplication windows | Recovery instances |

The [filesystem guard](../docs/extending-the-catalog.md#compose-a-filesystem-guard) combines a new domain and event package with the existing recovery policies. The [policy profile example](../docs/meta-reconfiguration.md) shows how a catalog can select different patterns while the CEP runs.

For example, `local_service_unavailable` is an observed event declared by the application domain. The `restart_confirmed_local_failure` CEP pattern combines it with application degradation and dependency availability. The policy mapping then requests `RESTART_LOCAL_SERVICE`, whose command handler verifies that a new worker generation is healthy.

Coordination patterns do not embed a platform command. The mapping declares the target domain and action type; the Executor handler owns the operation and its post-condition check. Blocking patterns can instead register an `E_block` lease without dispatching an action.

## Adapt a package to another system

1. Copy a relevant package and give it a name for the target deployment.
2. Replace metric endpoints, field mappings and handler bindings with actual interfaces.
3. Calibrate thresholds, cooldowns, local histories and CEP windows for that environment.
4. Select the package in a dedicated instance configuration and start the required producers.
5. Validate positive conditions, missing/forbidden context, action failures and timeouts.

Includes merge objects and concatenate lists; conflicting scalar values fail. Select a specialized package instead of including two conflicting versions. Endpoint/command substitutions use declared parameters such as `${GATEWAY_URL}`. Numeric thresholds remain typed JSON numbers.

The full [catalog extension guide](../docs/extending-the-catalog.md) covers an executable extra-domain example, deployment wiring, new operators and adapter tests. [Event contracts](../docs/event-contracts.md) describes the stream payloads that implementations must preserve.
