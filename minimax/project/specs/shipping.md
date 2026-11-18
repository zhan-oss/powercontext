# Shipping fee specification

Status: current. Task: `shipping-vip`.

- A regular order has free shipping when its subtotal is at least 10,000 cents.
- A VIP order has free shipping when its subtotal is at least 8,000 cents.
- Otherwise shipping costs 1,200 cents.
- A negative subtotal is invalid and raises `ValueError`.
- The boundary values are inclusive, and the existing regular-order behavior must remain valid.

