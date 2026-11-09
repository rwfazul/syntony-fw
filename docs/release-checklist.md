# Release checks

## Verify source and behavior

- Install development dependencies and a local `redis-server` executable.
- Run `make validate hygiene lint format-check test compose-check`.
- Validate the host-guard configuration and Compose override described in the catalog guide.
- Build the module images and run `make integration` against a fresh local instance.
- Check failure, timeout and verified recovery when changing an action adapter.

## Verify deployment assets

- Build a source bundle and check its SHA-256 manifests.
- Check the generated Grid topology in the intended reservation, including node identity, network bindings, image architecture and availability.
- Verify Ceph quorum and OSD health when using the Ceph example, then observe actual Syntony streams and the intended adaptation.
- Review adapter idempotency, authentication, state retention and resource limits for the selected environment.

## Prepare a release

- Choose the repository/registry namespace and version and record dependency/image versions.
- Confirm that configuration contains environment bindings suitable for distribution and no credentials or local data.
- Preserve the MIT license and applicable third-party notices.
- Describe the changes and the environments actually exercised in release notes.
- Publish the source and the corresponding module image set under the chosen version.
