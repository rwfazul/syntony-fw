# Specification operators

The PhD thesis uses logical and temporal notation to describe Syntony rules.
The catalog expresses those rules through Composer rule families, CEP pattern
families and policy constraints. The table maps each thesis operator to the
current implementation and identifies the scope of each correspondence.

## Thesis notation and catalog support

| Thesis notation | Meaning | Catalog representation and scope |
|---|---|---|
| `∧` | Conjunction | Composer `all_metrics` requires every metric predicate to hold in the latest local snapshot. `event_combo_all` requires all named observations within a local event-time window. CEP `participants_all` requires every domain-qualified participant in a bounded round window. |
| `∨` | Disjunction | Composer `any_metrics` accepts any configured metric predicate. CEP patterns are evaluated independently. There is no nested CEP disjunction expression. Separate patterns can request the same action through their policy mappings, with deduplication scoped to each policy. |
| `¬` | Negation | Composer comparisons include `neq`. CEP `forbidden_participants` vetoes a `participants_all` match when a listed observation exists in its window. Policy `required_flags` can require a flag to be `false`. Absence is evaluated within the selected stable context. There is no general expression-level `not`. |
| `⊕` | Temporal / same-round alignment | CEP `same_round_all` requires all named event types in the current stable round. For domain-qualified alignment, use `participants_all` with `within_rounds: 1`. A larger window allows observations from different rounds. |
| `□` | Invariance / always | The runtime evaluates guards at each admission and predicates over retained context. A universal invariance operator over all reachable states is not implemented. |
| `◇` | Eventuality / eventually | Execution outcomes can trigger later feedback patterns. A temporal eventuality operator, including obligation tracking and detection of an unmet obligation, is not implemented. |
| `U` | Persistence until | Named `E_block` guards remain active until a matching `E_unblock` or lease expiry. Boolean state can also be set on dispatch and cleared on a configured outcome. These mechanisms express particular guard lifecycles. A general temporal `until` expression is not implemented. |
| `⇒[t]` | Bounded temporal implication | Composer `event_sequence` detects an ordered sequence in `within_seconds` of local observed history. It recognizes completed sequences. It does not create an obligation after the antecedent or report a missing consequent at expiry. |
| `↦` | Condition-to-intent mapping | A matched CEP pattern emits the configured `coord_type`; its pattern name selects `coordinator.policy_mappings` for action-producing intents. |
| `↑` | Event emission | Composer, CEP and Executor publish observation, coordination and feedback envelopes to their Redis streams. This is an emission operation in the runtime. |
| `@t` | Time of occurrence | Metric and observation envelopes retain `event_time`. Coordination records retain round boundaries and provenance. Feedback uses completion/report time. |
| `state(x)` | Current state | Domain-qualified `state.flags` and policy `required_flags` expose current boolean admission context. Composer rules also read their latest metric snapshot. Arbitrary state expressions are not parsed from JSON. |
| `prev(x)` | Previous state | Composer keeps metric and observed-event histories; atomic state transitions read existing flags before updating them. A configurable `prev(x)` accessor and arbitrary current/previous-state comparisons are not implemented. |

Scalar comparisons `gt`, `gte`, `lt`, `lte`, `eq` and `neq` are
predicates within Composer rules. Conjunction, temporal alignment and policy
admission combine conditions at different stages of the pipeline.

## Control rounds and clock ticks

A control round is the event-time interval indexed by one row of the Round
Table. The CEP evaluates a round when the minimum required-domain watermark
makes it stable. A clock tick reports that progress.

Use `same_round_all` in new patterns. The spelling `same_tick_all` remains an
accepted alias. New catalog files use round names for configuration. Previous tick names are
accepted as aliases. Conflicting aliases are rejected. Configuration is
normalized to the existing persisted representation so equivalent round/tick
spellings preserve CEP checkpoint and policy-profile identities.

| Identifier | Meaning |
|---|---|
| `cep.round.seconds` | Duration of one control round in seconds |
| `cep.round.retention_rounds` | Number of rounds retained in the Round Table |
| `cep.relative_round_origin` | Select a round origin relative to the initial event-time frontier |
| `within_rounds` | CEP pattern window measured in rounds, including the evaluated round |
| `require_current_round` | Require at least one positive participant in the evaluated round |
| `tick`, `tick_start`, `tick_end` | Round index and its event-time boundaries in envelopes |
| `clock:tick` | Stream reporting closed control rounds |
| `payload.trigger_tick_end` | Boundary of the round that originated an executed action |

### Configuration aliases

| Preferred name | Accepted previous name |
|---|---|
| `cep.round` | `cep.tick` |
| `cep.round.retention_rounds` | `cep.tick.retention_ticks` |
| `cep.relative_round_origin` | `cep.relative_tick_origin` |
| `within_rounds` | `within_ticks` |
| `require_current_round` | `require_current_tick` |

The stream `clock:tick` reports clock progress. Envelope fields `tick`,
`tick_start`, `tick_end` and `trigger_tick_end` keep their published names.

## Domain-qualified same-round alignment

This pattern requires both observations in the same stable round and vetoes
the intent if a maintenance observation is present there.

```json
{
  "name": "restart_with_dependency_available",
  "type": "participants_all",
  "within_rounds": 1,
  "participants": [
    {"domain": "application_service", "event_type": "service_failed"},
    {"domain": "external_dependency", "event_type": "dependency_available"}
  ],
  "forbidden_participants": [
    {"domain": "application_service", "event_type": "maintenance_active"}
  ],
  "coord_type": "E_sync"
}
```

Declare the observation names in the domain Composer rules, include both domains
in `cep.watermark_domains` and provide the corresponding policy mapping before
using this illustrative pattern. The [supplied catalog examples](../specifications/README.md#supplied-examples)
provide complete instance selections.

Implementation entry points are `runtime/composer/rules.py`,
`runtime/composer/composer.py`, `runtime/cep/patterns.py` and
`runtime/internal_state/control.py`. See [algorithm alignment](algorithm-alignment.md)
for behavioral coverage and [event contracts](event-contracts.md) for envelopes.
