# Example specification packages

This directory contains reusable **examples** of domain, event and policy specifications. They configure the HTTP recovery application and the optional operating-system guard included in this repository. They are a starting point for an instance, not a complete catalog of integrations or a set of thresholds suitable for every environment.

The examples use real observations and verified action handlers. Reusing a package still requires binding it to the actual system, selecting appropriate thresholds and time windows, and checking the resulting control behavior.

## What each package describes

| Package | Role | Selection |
|---|---|---|
| `domains/application_service.json` | HTTP application metrics and local degradation, availability and fallback observations | Default instance |
| `domains/external_dependency.json` | Dependency health metrics and local availability/recovery observations | Default instance |
| `domains/operating_system.json` | Actual OS load and high/normal-load observations | Optional OS instance |
| `events/service_resilience.json` | Cross-domain patterns that identify dependency failure, local-process failure and recovery | Default instance |
| `events/host_maintenance_guard.json` | A named restart guard with blocking, explicit release and TTL expiry | Optional OS instance |
| `policies/service_resilience.json` | Pattern-to-action mappings, command handlers and deduplication windows | Recovery instances |

The [filesystem guard](../docs/extending-the-catalog.md#compose-a-filesystem-guard) combines a new domain and event package with the existing recovery policies. The [policy profile example](../docs/meta-reconfiguration.md) shows how a catalog can select different patterns while the CEP runs.

The three subdirectories correspond to different responsibilities:

- **Domains:** where metrics come from and which local conditions become observed events.
- **Events:** which combinations of observations produce coordination events.
- **Policies:** which concrete actions those coordination decisions request and how they are executed.

The runtime implements the operators used by these declarations. Adding JSON does not create a new source protocol, event operator or infrastructure adapter.

## How packages are selected

Only packages referenced by an instance configuration are loaded. Placing a JSON file here does not activate it automatically.

The default instance is selected by [config/syntony_config.json](../config/syntony_config.json). The optional [config/service-resilience-host-guard.json](../config/service-resilience-host-guard.json) includes that instance, the OS domain and the host-pressure guard. It also adds the OS domain to the required watermark set.

```bash
python -m runtime validate --config config/syntony_config.json
python -m runtime validate --config config/service-resilience-host-guard.json
```

Run these commands from the repository root. Validation assembles includes and checks the references; it does not start a Monitor or deploy services. Every active domain also needs its Monitor and Composer, as shown in the [extension guide](../docs/extending-the-catalog.md).

## Follow the references between files

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

For example, `local_service_unavailable` is an observed event declared by the application domain. The `restart_confirmed_local_failure` CEP pattern combines it with application degradation and dependency availability. The policy mapping then requests `RESTART_LOCAL_SERVICE`, whose command handler verifies that a new worker generation is healthy.

Coordination patterns do not embed a platform command. The mapping declares the target domain and action type; the Executor handler owns the operation and its post-condition check. Blocking patterns can instead register an `E_block` lease without dispatching an action.

## Adapt a package to another system

1. Copy a relevant example to a new package name rather than changing the supplied example for unrelated deployments.
2. Replace metric endpoints, field mappings and handler bindings with actual interfaces.
3. Calibrate thresholds, cooldowns, local histories and CEP windows for that environment.
4. Select the package in a dedicated instance configuration and start the required producers.
5. Validate positive conditions, missing/forbidden context, action failures and timeouts.

Includes merge objects and concatenate lists; conflicting scalar values fail. Select a specialized package instead of including two conflicting versions. Endpoint/command substitutions use declared parameters such as `${GATEWAY_URL}`. Numeric thresholds remain typed JSON numbers.

The full [catalog extension guide](../docs/extending-the-catalog.md) covers an executable extra-domain example, deployment wiring, new operators and adapter tests. [Event contracts](../docs/event-contracts.md) describes the stream payloads that implementations must preserve.
