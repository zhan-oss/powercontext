# MiniMax SDD context example

This directory is a small, reproducible engineering task for the 2026
OceanBase MiniMax challenge. The context CLI stores the specification, source
code, and tests in embedded seekdb, then queries seekdb to assemble the task
context. It saves task progress in PowerContext Memory and reads it in later
processes. `SCENARIO.md` states the required sources and expected behavior.

## Requirements

- Python 3.12, `pylibseekdb==1.4.0.post2`, `PyMySQL>=1.1,<2`.
- `powercontext[builtin]==1.2.0` from PyPI.
- MiniMax development session: linked from `records/minimax-session.md`.

On a supported development host, from the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install 'powercontext[builtin]==1.2.0' 'pylibseekdb==1.4.0.post2' 'PyMySQL>=1.1,<2'
python minimax/context_cli.py import --data minimax/.run --task shipping-vip
python minimax/context_cli.py context --data minimax/.run --task shipping-vip > minimax/.run/context.json
python minimax/build_prompt.py minimax/.run/context.json > minimax/.run/minimax-request.txt
cd minimax/project
python -m unittest -v test_shipping
```

The context and prompt are constructed from the **actual seekdb query output**,
with the three source paths and hashes. The committed MiniMax change makes all
three tests pass. `records/shipping_before.py` is the original source snapshot;
the GitHub Actions workflow temporarily substitutes it, runs the same tests,
confirms only the VIP target fails, restores the MiniMax source, then confirms
all three pass. It also tests specification reimport with a changed threshold.

Return to the repository root and record the observed result:

```bash
python minimax/context_cli.py import --data minimax/.run --task shipping-vip
python minimax/context_cli.py record --data minimax/.run --task shipping-vip \
  --done 'MiniMax changed the VIP shipping threshold' \
  --next 'Review the updated specification and related tests' \
  --verification 'python -m unittest -v test_shipping: 3 passed'
python minimax/context_cli.py context --data minimax/.run --task shipping-vip > minimax/.run/context-after.json
```

The final `context` command starts a new PowerContext runtime, queries seekdb
for current engineering sources, and searches persisted PowerContext Memory
for the prior progress record. The output contains each source path, kind,
hash, and current text, plus `previous_state` from PowerContext.

## Current status

The MiniMax session, before/after source, and integration logs are linked in
`records/`. The exact code version and platform score are recorded there after
the final workflow and training-camp evaluation finish.
