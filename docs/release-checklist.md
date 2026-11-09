# Release checks

## Verify source and behavior

- Install development dependencies and a local `redis-server` executable.
- Run `make validate hygiene lint format-check test compose-check`.
- Validate the example instance configurations and their Compose overrides.
- Build the module images and run `make integration` against a fresh local instance.
- Check failure, timeout and verified recovery when changing an action adapter.

## Verify deployment assets

- Build a source bundle and check its SHA-256 manifests.
- For the Syntony recovery example, verify that the service recovers and that the Executor publishes the corresponding feedback.
- Review adapter idempotency, authentication, state retention and resource limits for the selected environment.

## Prepare a release

- Choose the repository/registry namespace and version and record dependency/image versions.
- Confirm that configuration contains environment bindings suitable for distribution and no credentials or local data.
- Preserve the MIT license and applicable third-party notices.
- Describe the changes and the environments actually exercised in release notes.
- Publish the source and the corresponding module image set under the chosen version.
