"""Shipping quote rules for the small order service."""


def shipping_fee_cents(subtotal_cents: int, *, vip: bool = False) -> int:
    if subtotal_cents < 0:
        raise ValueError("subtotal must be nonnegative")
    threshold_cents = 8_000 if vip else 10_000
    if subtotal_cents >= threshold_cents:
        return 0
    return 1_200

