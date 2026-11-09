# Contributing

Use Python 3.11 or newer. Install an editable checkout with `python -m pip install -e '.[dev]'`, install `redis-server` (or set `REDIS_SERVER` to its executable), then run `make validate hygiene lint format-check test compose-check`. Run `make integration` explicitly when changing executable Docker examples; it builds the module images and performs an actual recovery check.

Keep reusable definitions in `specifications/`, instance selection in `config/`, mechanisms in `runtime/`, and infrastructure examples in `deployment/`. See [the extension guide](docs/extending-the-catalog.md) before adding domains, events, patterns or handlers.

Add a direct runtime test for a new operator or failure path. Use test doubles only in tests. Do not add random metric fallbacks, simulated action success, credentials, measured datasets or campaign outputs to the distributed runtime. Keep all documentation, comments and public diagnostics in English.

Document the exact validation performed. Offline descriptor checks do not establish a working Grid deployment. Preserve third-party notices when reusing code. Contributions are provided under the repository's MIT license.

## Code style and comments

Run `make format` to apply the repository's Python layout and `make lint format-check` to check imports, undefined names and formatting. Ruff is a development-only dependency. Keep module docstrings focused on responsibilities, inputs/outputs and operating constraints; explain technical decisions where they help the reader. Describe the current behavior without development chronology.

## Generated packaging files

Editable installs and package builds can create `build/` and `*.egg-info/`. These directories are generated, ignored, and excluded from the deployment bundle. `dependency_links.txt` is legacy setuptools metadata and may be empty; it is not the dependency list to edit. Runtime dependencies are declared in `pyproject.toml` and each module's `requirements.txt`. Rebuild packaging metadata when needed rather than committing or manually filling those generated files.
