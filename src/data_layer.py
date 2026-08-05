"""Deterministic data access: loads Olist CSVs once and derives all verifiable
per-case facts (joins, delivery/handoff variance, payment reconciliation,
customer history, product context). No LLM involved here on purpose -- these
are mechanical joins/arithmetic, and the lab brief explicitly asks to prefer
verifiable data over invented facts.
"""
import os
from dataclasses import dataclass, field

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def round2(x):
    return None if x is None else round(float(x), 2)


class DataStore:
    def __init__(self, data_dir: str = DATA_DIR):
        self.orders = pd.read_csv(os.path.join(data_dir, "olist_orders_dataset.csv"), dtype=str).set_index("order_id", drop=False)
        self.items = pd.read_csv(os.path.join(data_dir, "olist_order_items_dataset.csv"), dtype=str)
        self.items["order_item_id"] = self.items["order_item_id"].astype(int)
        self.items["price"] = self.items["price"].astype(float)
        self.items["freight_value"] = self.items["freight_value"].astype(float)
        self.payments = pd.read_csv(os.path.join(data_dir, "olist_order_payments_dataset.csv"), dtype=str)
        self.payments["payment_sequential"] = self.payments["payment_sequential"].astype(int)
        self.payments["payment_value"] = self.payments["payment_value"].astype(float)
        self.customers = pd.read_csv(os.path.join(data_dir, "olist_customers_dataset.csv"), dtype=str).set_index("customer_id", drop=False)
        self.products = pd.read_csv(os.path.join(data_dir, "olist_products_dataset.csv"), dtype=str).set_index("product_id", drop=False)


def _dedup_stable(values):
    seen = []
    for v in values:
        if v is not None and v not in seen:
            seen.append(v)
    return seen


def _hours_between(later, earlier):
    if later is None or earlier is None:
        return None
    return round2((pd.Timestamp(later) - pd.Timestamp(earlier)).total_seconds() / 3600)


@dataclass
class CaseFacts:
    order_id: str
    order_found: bool
    order_status: str = None
    customer_id: str = None
    customer_unique_id: str = None
    related_order_ids: list = field(default_factory=list)
    repeat_customer: bool = False

    item_rows: list = field(default_factory=list)          # [{order_item_id, product_id, seller_id, price, freight_value, shipping_limit_date}]
    seller_ids: list = field(default_factory=list)          # distinct, stable order, all sellers on the order
    product_ids: list = field(default_factory=list)
    category_names: list = field(default_factory=list)
    multiple_categories: bool = False

    payment_rows: list = field(default_factory=list)        # [{payment_sequential, payment_type, payment_value}]
    payment_types: list = field(default_factory=list)
    item_total_brl: float = None
    freight_total_brl: float = None
    expected_total_brl: float = None
    payment_total_brl: float = None
    difference_brl: float = None
    reconciled: bool = None

    delivered_at: str = None
    estimated_delivery_at: str = None
    carrier_handoff_at: str = None
    delivery_variance_hours: float = None
    late_delivery: bool = None
    seller_handoff_analysis: list = field(default_factory=list)
    late_handoff_seller_ids: list = field(default_factory=list)

    multi_item_order: bool = False
    multi_seller_order: bool = False
    split_payment: bool = False


def build_case_facts(store: DataStore, claimed_order_id: str) -> CaseFacts:
    if claimed_order_id not in store.orders.index:
        return CaseFacts(order_id=claimed_order_id, order_found=False)

    order = store.orders.loc[claimed_order_id]
    facts = CaseFacts(order_id=claimed_order_id, order_found=True, order_status=order["order_status"])

    # --- customer identity + history (all other orders of the same real-world customer) ---
    customer_id = order["customer_id"]
    facts.customer_id = customer_id
    if customer_id in store.customers.index:
        cust = store.customers.loc[customer_id]
        facts.customer_unique_id = cust["customer_unique_id"]
        sibling_customer_ids = set(store.customers.index[store.customers["customer_unique_id"] == facts.customer_unique_id])
        sibling_orders = store.orders[store.orders["customer_id"].isin(sibling_customer_ids)]
        sibling_orders = sibling_orders[sibling_orders["order_id"] != claimed_order_id]
        sibling_orders = sibling_orders.sort_values("order_purchase_timestamp")
        all_related = list(sibling_orders["order_id"])
        facts.repeat_customer = len(all_related) > 0
        facts.related_order_ids = all_related[:5]

    # --- items / sellers / products / categories ---
    items = store.items[store.items["order_id"] == claimed_order_id].sort_values("order_item_id")
    for _, row in items.iterrows():
        facts.item_rows.append({
            "order_item_id": int(row["order_item_id"]),
            "product_id": row["product_id"],
            "seller_id": row["seller_id"],
            "price": round2(row["price"]),
            "freight_value": round2(row["freight_value"]),
            "shipping_limit_date": row["shipping_limit_date"],
        })
    facts.seller_ids = _dedup_stable(items["seller_id"].tolist())
    facts.product_ids = _dedup_stable(items["product_id"].tolist())
    facts.multi_item_order = len(items) >= 2
    facts.multi_seller_order = len(facts.seller_ids) >= 2

    categories = []
    for pid in facts.product_ids:
        if pid in store.products.index:
            cat = store.products.loc[pid]["product_category_name"]
            if isinstance(cat, str) and cat:
                categories.append(cat)
    facts.category_names = _dedup_stable(categories)
    facts.multiple_categories = len(facts.category_names) >= 2

    # --- payments / reconciliation ---
    payments = store.payments[store.payments["order_id"] == claimed_order_id].sort_values("payment_sequential")
    for _, row in payments.iterrows():
        facts.payment_rows.append({
            "payment_sequential": int(row["payment_sequential"]),
            "payment_type": row["payment_type"],
            "payment_value": round2(row["payment_value"]),
        })
    facts.payment_types = _dedup_stable(payments["payment_type"].tolist())
    facts.payment_total_brl = round2(payments["payment_value"].sum()) if len(payments) else 0.0
    facts.split_payment = len(payments) >= 2

    facts.item_total_brl = round2(items["price"].sum()) if len(items) else 0.0
    facts.freight_total_brl = round2(items["freight_value"].sum()) if len(items) else 0.0
    if len(items):
        facts.expected_total_brl = round2(facts.item_total_brl + facts.freight_total_brl)
        facts.difference_brl = round2(facts.payment_total_brl - facts.expected_total_brl)
        facts.reconciled = abs(facts.difference_brl) <= 0.10
    # else: expected_total_brl / difference_brl / reconciled stay None per spec (sum is defined,
    # but "matches expectation" is not, with zero item rows to compare against)

    # --- delivery variance ---
    facts.delivered_at = order["order_delivered_customer_date"] if pd.notna(order["order_delivered_customer_date"]) else None
    facts.estimated_delivery_at = order["order_estimated_delivery_date"] if pd.notna(order["order_estimated_delivery_date"]) else None
    facts.carrier_handoff_at = order["order_delivered_carrier_date"] if pd.notna(order["order_delivered_carrier_date"]) else None
    facts.delivery_variance_hours = _hours_between(facts.delivered_at, facts.estimated_delivery_at)
    facts.late_delivery = None if facts.delivery_variance_hours is None else facts.delivery_variance_hours > 0

    # --- per-seller handoff variance (earliest shipping_limit_date per seller vs carrier handoff) ---
    if len(items) and facts.carrier_handoff_at is not None:
        for seller_id in facts.seller_ids:
            seller_items = items[items["seller_id"] == seller_id]
            earliest_limit = seller_items["shipping_limit_date"].min()
            variance = _hours_between(facts.carrier_handoff_at, earliest_limit)
            late = variance is not None and variance > 0
            facts.seller_handoff_analysis.append({
                "seller_id": seller_id,
                "shipping_limit_at": earliest_limit,
                "handoff_variance_hours": variance,
                "late_handoff": late,
            })
            if late:
                facts.late_handoff_seller_ids.append(seller_id)
    elif len(items):
        for seller_id in facts.seller_ids:
            seller_items = items[items["seller_id"] == seller_id]
            earliest_limit = seller_items["shipping_limit_date"].min()
            facts.seller_handoff_analysis.append({
                "seller_id": seller_id,
                "shipping_limit_at": earliest_limit,
                "handoff_variance_hours": None,
                "late_handoff": False,
            })

    return facts
