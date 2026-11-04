"""Load the store's CSV exports into typed records and report data-quality issues.

The CSVs are treated as read-only exports of the store's system. Nothing is
fixed silently: inconsistencies are collected in Store.issues so they can be
reviewed (`python -m emporio.data`) instead of being papered over.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .pricing import brl, max_installments, money


def norm(text) -> str:
    """Lowercase, accent-free, single-spaced. Used for matching only, never for display."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category_id: int
    price: Decimal
    description: str
    stock: int
    status: str  # active | discontinued | coming_soon
    specs: dict
    created_at: date | None

    @property
    def availability(self) -> str:
        if self.status == "discontinued":
            return "descontinuado"
        if self.status == "coming_soon":
            return "em breve (ainda não disponível para venda)"
        if self.stock <= 0:
            return "esgotado"
        return "disponível"

    @property
    def available(self) -> bool:
        return self.availability == "disponível"


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    description: str
    active: bool


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass
class Order:
    id: int
    customer_id: int
    order_date: date
    status: str  # pending | confirmed | shipped | delivered | cancelled
    total: Decimal
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: list[OrderItem] = field(default_factory=list)


@dataclass
class Store:
    categories: dict[int, Category]
    products: dict[int, Product]
    promotions: list[Promotion]
    customers: dict[int, Customer]
    orders: dict[int, Order]
    issues: list[str] = field(default_factory=list)

    def active_promotion(self, product_id: int) -> Promotion | None:
        """Promotions don't stack (policy 6.2). If several were active, the best one for the customer applies."""
        active = [p for p in self.promotions if p.product_id == product_id and p.active]
        return max(active, key=lambda p: p.discount_percent) if active else None

    def category_name(self, category_id: int) -> str:
        category = self.categories.get(category_id)
        return category.name if category else "Sem categoria"


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_store(data_dir: Path) -> Store:
    categories = {
        int(r["category_id"]): Category(int(r["category_id"]), r["name"], r["description"])
        for r in _rows(data_dir / "categories.csv")
    }
    products = {}
    for r in _rows(data_dir / "products.csv"):
        products[int(r["product_id"])] = Product(
            id=int(r["product_id"]),
            name=r["name"].strip(),
            category_id=int(r["category_id"]),
            price=money(r["price_brl"]),
            description=r["description"].strip(),
            stock=int(r["stock_quantity"]),
            status=r["status"].strip(),
            specs=json.loads(r["specs"]) if r["specs"] else {},
            created_at=_date(r["created_at"]),
        )
    promotions = [
        Promotion(
            int(r["promotion_id"]), int(r["product_id"]), int(r["discount_percent"]),
            r["description"].strip(), r["is_active"].strip() == "1",
        )
        for r in _rows(data_dir / "promotions.csv")
    ]
    customers = {
        int(r["customer_id"]): Customer(
            int(r["customer_id"]), r["name"].strip(), r["phone"].strip(), r["email"].strip().lower(), r["city"].strip()
        )
        for r in _rows(data_dir / "customers.csv")
    }
    orders = {}
    for r in _rows(data_dir / "orders.csv"):
        orders[int(r["order_id"])] = Order(
            id=int(r["order_id"]),
            customer_id=int(r["customer_id"]),
            order_date=date.fromisoformat(r["order_date"]),
            status=r["status"].strip(),
            total=money(r["total_brl"]),
            payment_method=r["payment_method"].strip(),
            tracking_code=r["tracking_code"].strip() or None,
            estimated_delivery=_date(r["estimated_delivery"].strip()),
            notes=r["notes"].strip() or None,
        )
    for r in _rows(data_dir / "order_items.csv"):
        orders[int(r["order_id"])].items.append(OrderItem(int(r["product_id"]), int(r["quantity"])))

    store = Store(categories, products, promotions, customers, orders)
    store.issues = quality_report(store)
    return store


def quality_report(store: Store) -> list[str]:
    issues: list[str] = []

    for order in sorted(store.orders.values(), key=lambda o: o.id):
        expected = Decimal("0")
        for item in order.items:
            product = store.products.get(item.product_id)
            if product is None:
                issues.append(f"Pedido {order.id}: produto {item.product_id} não existe no catálogo.")
                continue
            expected += product.price * item.quantity
            if product.status == "coming_soon" and order.status in ("shipped", "delivered"):
                issues.append(
                    f"Pedido {order.id}: '{product.name}' está como 'coming_soon' no catálogo, "
                    f"mas o pedido já consta como '{order.status}'."
                )
        if order.items and money(expected) != order.total:
            issues.append(
                f"Pedido {order.id}: total registrado {brl(order.total)} difere da soma dos itens "
                f"a preço atual ({brl(expected)}). Pode ser desconto ou mudança de preço; não há campo que explique."
            )
        credit = re.fullmatch(r"credit_(\d+)x", order.payment_method)
        if credit and int(credit.group(1)) > max_installments(order.total):
            n = int(credit.group(1))
            issues.append(
                f"Pedido {order.id}: {n}x de {brl(order.total / n)} fica abaixo da parcela mínima "
                f"da política atual (máximo permitido para {brl(order.total)}: {max_installments(order.total)}x)."
            )
        if order.status in ("shipped", "delivered") and not order.tracking_code:
            issues.append(f"Pedido {order.id}: '{order.status}' sem código de rastreio.")
        if order.customer_id not in store.customers:
            issues.append(f"Pedido {order.id}: cliente {order.customer_id} não existe.")

    used = Counter(p.category_id for p in store.products.values())
    for category in store.categories.values():
        if not used[category.id]:
            issues.append(f"Categoria '{category.name}' existe, mas não tem nenhum produto cadastrado.")

    per_product = Counter(p.product_id for p in store.promotions if p.active)
    for product_id, count in per_product.items():
        if count > 1:
            issues.append(f"Produto {product_id}: {count} promoções ativas ao mesmo tempo (não acumulam).")
    for promo in store.promotions:
        product = store.products.get(promo.product_id)
        if product is None:
            issues.append(f"Promoção {promo.id}: produto {promo.product_id} não existe.")
        elif promo.active and not product.available:
            issues.append(f"Promoção {promo.id} ativa para '{product.name}', que está {product.availability}.")

    for field_name in ("email", "phone"):
        counts = Counter(getattr(c, field_name) for c in store.customers.values())
        for value, count in counts.items():
            if count > 1:
                issues.append(f"Clientes: {field_name} '{value}' aparece em {count} cadastros (verificação ambígua).")

    return issues


if __name__ == "__main__":
    from .config import load_settings

    s = load_store(load_settings().data_dir)
    print(
        f"{len(s.products)} produtos · {len(s.categories)} categorias · {len(s.customers)} clientes · "
        f"{len(s.orders)} pedidos · {sum(p.active for p in s.promotions)} promoções ativas\n"
    )
    print(f"{len(s.issues)} achados de qualidade de dados:")
    for issue in s.issues:
        print(f"- {issue}")
