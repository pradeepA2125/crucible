"""The cart."""
from dataclasses import dataclass, field

from shop.pricing import apply_discount, line_total


@dataclass
class Item:
    name: str
    price: float
    quantity: int


@dataclass
class Cart:
    items: list[Item] = field(default_factory=list)

    def add(self, name: str, price: float, quantity: int = 1) -> None:
        for item in self.items:
            if item.name == name:
                item.quantity += quantity
                return
        self.items.append(Item(name, price, quantity))

    def total(self, discount_code: str | None = None) -> float:
        subtotal = sum(line_total(i.price, i.quantity) for i in self.items)
        return round(apply_discount(subtotal, discount_code), 2)
