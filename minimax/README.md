# MiniMax SDD context example

This directory is a small, reproducible engineering task for the 2026
OceanBase MiniMax challenge. The context CLI stores the specification, source
code, and tests in embedded seekdb, then queries seekdb to assemble the task
context. It saves task progress in PowerContext Memory and reads it in later
processes. `SCENARIO.md` states the required sources and expected behavior.

## Requirements

- Python 3.12, `pylibseekdb==1.4.0.post2`, `PyMySQL>=1.1,<2`.
- `powercontext[builtin]==1.2.0` from PyPI.
- An account with MiniMax Code access for the actual code modification.

On a supported development host, from the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install 'powercontext[builtin]==1.2.0' 'pylibseekdb==1.4.0.post2' 'PyMySQL>=1.1,<2'
python minimax/context_cli.py import --data minimax/.run --task shipping-vip
python minimax/context_cli.py context --data minimax/.run --task shipping-vip > minimax/.run/context-before.json
python minimax/build_prompt.py minimax/.run/context-before.json > minimax/.run/minimax-request.txt
cd minimax/project
python -m unittest -v test_shipping
```

Give `minimax/.run/minimax-request.txt` to MiniMax Code, and save the resulting
conversation in `records/`. The prompt is constructed from the **actual seekdb
query output**, with the three source paths and hashes. The last command
intentionally fails **only** `test_vip_threshold` before the
MiniMax change. The other two tests are regression controls. Return to the
repository root before invoking the context CLI again.

After MiniMax modifies `project/shipping.py`, rerun the **same** test command.
Then import the changed code and record the observed result:

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

The base example and failing target test are prepared. The required MiniMax
session, seekdb execution log, PowerContext restart log, final code change,
and passing test log must be added before submission. Do not treat this scaffold
as a completed entry.

