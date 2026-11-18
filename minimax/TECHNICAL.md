# SDD context module: technical summary

The CLI entry point is `context_cli.py`. Its input is a task key, a project
directory with `manifest.json`, and a persistent data directory. `import`
reads the manifest's current specification, source, and test files and stores
their paths, kinds, contents, and SHA-256 hashes in embedded seekdb.
`context` executes a SQL query scoped by task key and returns the current
seekdb rows in specification, code, test order. `build_prompt.py` converts
that output into a MiniMax development request. A specification edit followed
by `import` replaces the stored row and changes the next result.

The same CLI stores a task progress record in PowerContext Memory via
`RememberMemoryRequest`. A later `context` invocation opens the persisted
PowerContext runtime in a new process, searches the task's scope, and returns
the prior record in `previous_state`. This is state reuse across runs, not
reconstruction from an old chat transcript.

MiniMax Code receives the request generated from the seekdb result. In the
recorded session it changed the VIP shipping threshold in `project/shipping.py`
without editing the tests. The target test fails against the original source
snapshot and all three tests pass against the MiniMax source. The regular
shipping threshold and negative-input behavior are regression controls.

seekdb is responsible for engineering-source storage and retrieval;
PowerContext is responsible for durable task state; MiniMax is responsible
for the demonstrated source modification. `records/` links the development
session, original source, database and state checks, and test outcomes.
