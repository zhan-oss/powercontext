# Verification scenario: VIP shipping threshold

Task key: `shipping-vip`.

The current specification is `project/specs/shipping.md`. The existing code is
`project/shipping.py`; tests are `project/test_shipping.py`. Before the change,
`test_vip_threshold` fails because an 8,000-cent VIP order is charged 1,200 cents.
`test_regular_threshold` and `test_negative_subtotal` pass.

The context module must import those three files into seekdb and return their
actual contents and SHA-256 hashes when asked for `shipping-vip`. The MiniMax
development request will include this returned context. MiniMax must change
`project/shipping.py` so the same target test passes while the two regression
tests remain green.

After recording the completed step and test result in PowerContext, a new
process must read that state and include it in its context output.

To verify a specification update, change the VIP threshold in
`project/specs/shipping.md`, run `import` again, and check that `context` returns
the new text and hash. Restore the original specification afterward and import
again. The module must not return a fixed copy of the old specification.

To reset local state, remove only the chosen `.run` directory. The code and
specification files in `project/` remain intact.

