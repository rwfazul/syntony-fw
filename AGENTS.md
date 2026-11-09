# Syntony

- Keep implementation, configuration, documentation and deployment examples self-contained.
- Configuration: `config/`; reusable catalog: `specifications/`; executable modules: `runtime/`.
- `make hygiene` checks required files, headings, local links and syntax.
- `make validate` validates references and supported adapters. `make test` tests the runtime and real local HTTP/process boundaries.
- Do not add simulated sources, implicit success, shell interpolation, or undocumented connector stubs to runtime.
- Internal state is a logical layer. Initial deployment supports one Coordinator and one Executor per Redis instance.
- Keep comments and docstrings focused on behavior and interfaces. Run `make lint format-check` after Python changes.
- Keep `docs/algorithm-alignment.md` and the README accurate when behavior changes.
- Docker/Grid execution must be reported separately from static descriptor and unit tests.
