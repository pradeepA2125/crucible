"""Prices and discount codes."""

DISCOUNTS: dict[str, float] = {"SAVE10": 0.10, "HALF": 0.50}


def line_total(price: float, quantity: int) -> float:
    return price * quantity


def apply_discount(amount: float, code: str | None) -> float:
    """The amount after the discount `code` gives; unknown or missing codes give none."""
    if code is None or code not in DISCOUNTS:
        return amount
    return amount * DISCOUNTS[code]
