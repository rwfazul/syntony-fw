# Documentation

Start with the [project README](../README.md), then follow the path that fits your task.

## Instantiate and run

1. Read [runtime modules](modules.md) and [event contracts](event-contracts.md) for component roles and event flow.
2. Use the [instance configuration guide](../config/README.md) to select catalog packages and bind endpoints.
3. Configure [policy state and recovery](state-and-policies.md) for admission and execution outcomes.
4. Run the HTTP recovery example with the [local deployment guide](../deployment/examples/local/README.md).

## Extend Syntony

Start with [the Specification Catalog](../specifications/README.md), then follow [the Specification Catalog guide](extending-the-catalog.md): select packages, add a domain, define events and policies, bind real handlers, start the corresponding producers, and validate the instance. The [monitor examples](../deployment/examples/monitors/README.md) cover real command, HTTP and OS acquisition. The host-pressure guard shows how to add a domain to a complete instance.

The guide distinguishes declarative extensions from source/operator implementations that require Python code and tests. Domain expertise and calibration remain the implementer's responsibility.

## Deploy on reserved infrastructure

Read the [Grid’5000 overview](../deployment/examples/grid5000/README.md), then select the compact Syntony/CEP profile or the [six-node Ceph/Syntony deployment example](../deployment/examples/grid5000/ceph/README.md). Generated descriptors and offline checks are preparation; they are not a substitute for a live reservation test.

## Review and contribute

- [Algorithm alignment](algorithm-alignment.md): implementation/test correspondence and explicit boundaries.
- [Release checklist](release-checklist.md): steps before release.
- [Contribution guide](../CONTRIBUTING.md): development and validation commands.
- [MIT license](../LICENSE): reuse terms for this source distribution.
