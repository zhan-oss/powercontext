"""Shipping quote rules for the small order service."""


def shipping_fee_cents(subtotal_cents: int, *, vip: bool = False) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal must be nonnegative")
    if subtotal_cents >= 10_000:
        return 0
    return 1_200

