# Verification record

## Baseline functionality, before the MiniMax change

Code: `project/shipping.py` as committed in `3209e651307a63025f9e4d65fba214fa91a0326c`.

Command: `python -m unittest -v test_shipping`, run from `minimax/project` on Python 3.12.10.

Exit code: 1. `test_vip_threshold` failed because an 8,000-cent VIP order returned 1,200 cents instead of 0. `test_regular_threshold` and `test_negative_subtotal` passed. This is a functional failure, not an environment or dependency error.

## seekdb and PowerContext integration

GitHub Actions run: https://github.com/zhan-oss/powercontext/actions/runs/44235872589

Commit: `e19e90e4765fee3c0f4f2685581d81e511d019b4`.

Job `context` completed successfully on Ubuntu with Python 3.12. The workflow installed the documented `pylibseekdb`, `PyMySQL`, and `powercontext[builtin]` versions; imported all three source files; queried them from seekdb; saved a task record in PowerContext; and read it in a fresh process. The job log ends with `seekdb sources and PowerContext restart: passed`.

Follow-up run: https://github.com/zhan-oss/powercontext/actions/runs/44236103777

Commit: `551a6e89fd78d63ccffb7f6afe6db178e46bb1e9`. The clean Linux job repeated the same database and PowerContext checks, reproduced the target test failure with the two regression tests passing, changed the specification in its disposable checkout, reimported it, and confirmed that a new seekdb query returned the changed text and hash. Its log prints `baseline target behavior: failed as expected` and `specification reimport and refreshed query: passed`.

## Pending evidence

- MiniMax Code development conversation showing the context supplied and the change it made.
- The resulting source diff and the same three tests passing after the change.

