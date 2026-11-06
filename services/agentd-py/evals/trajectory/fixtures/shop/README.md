# shop

A tiny shopping cart: add items, then ask the cart for its total, optionally with a discount
code.

```python
from shop.cart import Cart

cart = Cart()
cart.add("pen", price=2.50, quantity=4)
cart.total(discount_code="SAVE10")
```

Run the tests with `python -m pytest -q`.
