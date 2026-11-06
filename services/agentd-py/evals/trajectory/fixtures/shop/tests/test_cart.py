from shop.cart import Cart


def test_total_without_discount() -> None:
    cart = Cart()
    cart.add("pen", price=2.5, quantity=4)
    cart.add("pad", price=5.0)
    assert cart.total() == 15.0


def test_adding_the_same_item_twice_adds_to_its_quantity() -> None:
    cart = Cart()
    cart.add("pen", price=2.5)
    cart.add("pen", price=2.5, quantity=2)
    assert [(i.name, i.quantity) for i in cart.items] == [("pen", 3)]


def test_unknown_code_gives_no_discount() -> None:
    cart = Cart()
    cart.add("pad", price=5.0, quantity=2)
    assert cart.total(discount_code="NOPE") == 10.0


def test_total_with_discount() -> None:
    cart = Cart()
    cart.add("pad", price=5.0, quantity=20)
    assert cart.total(discount_code="SAVE10") == 90.0
