"""EC_POLICY_V2 -- deterministic ground truth. This is the ONE authoritative
implementation of the rule table in README section 4; the Policy Agent (LLM)
proposes a classification independently and the Verifier reconciles against
this engine so a model slip can never corrupt the written output.
"""
from data_layer import CaseFacts, round2

PLATFORM = {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
LOGISTICS = {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}

LIMITS = dict(order_ids=5, item_ids=5, seller_ids=3, payment_ids=5, related_order_ids=5,
              product_ids=5, category_names=5, root_causes=3, responsible_parties=3,
              evidence_ids=20, resolution_actions=5)


def cap(seq, key):
    return list(seq)[: LIMITS[key]]


def classify(facts: CaseFacts):
    """Returns (primary_issue, root_cause_code, responsible_parties, refund_amount, primary_action)."""
    if facts.order_status == "canceled" and (facts.payment_total_brl or 0) > 0:
        return ("canceled_order_paid", "ORDER_CANCELED_AFTER_PAYMENT", [dict(PLATFORM)],
                facts.payment_total_brl, "issue_full_refund")

    if facts.order_status == "unavailable" and (facts.payment_total_brl or 0) > 0:
        return ("unavailable_order_paid", "ORDER_UNAVAILABLE_AFTER_PAYMENT", [dict(PLATFORM)],
                facts.payment_total_brl, "issue_full_refund")

    if facts.late_delivery is True and len(facts.late_handoff_seller_ids) > 0:
        resp = [{"party_type": "seller", "party_id": sid} for sid in cap(facts.late_handoff_seller_ids, "responsible_parties")]
        return ("late_delivery_seller", "SELLER_HANDOFF_AFTER_LIMIT", resp,
                facts.freight_total_brl, "refund_freight")

    if facts.late_delivery is True and len(facts.late_handoff_seller_ids) == 0:
        return ("late_delivery_logistics", "CARRIER_DELIVERED_AFTER_ESTIMATE", [dict(LOGISTICS)],
                facts.freight_total_brl, "refund_freight")

    if facts.split_payment and facts.reconciled is True:
        return ("valid_split_payment", "MULTIPLE_PAYMENTS_RECONCILED", [], 0.0,
                "explain_valid_split_payment")

    if facts.late_delivery is False and facts.reconciled is True:
        return ("unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", [], 0.0,
                "reject_late_refund")

    # Not expected to trigger on the 50 lab cases (verified against data); kept only
    # so a future/unseen case degrades to a safe no_action instead of crashing the batch.
    return ("unsupported_late_claim", "DELIVERY_WITHIN_ESTIMATE", [], 0.0, "reject_late_refund")


def secondary_issues(facts: CaseFacts):
    issues = []
    if facts.multi_item_order:
        issues.append("multi_item_order")
    if facts.multi_seller_order:
        issues.append("multi_seller_order")
    if facts.split_payment:
        issues.append("split_payment")
    if facts.repeat_customer:
        issues.append("repeat_customer")
    if facts.multiple_categories:
        issues.append("multiple_categories")
    return issues


def resolution_actions(primary_action, primary_issue, case_status, secondary, split_payment):
    actions = [primary_action]
    if primary_issue == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary_issue == "late_delivery_logistics":
        actions.append("review_carrier_delay")
    if case_status == "action_required":
        actions.append("verify_refund_completion")
    if "multi_seller_order" in secondary:
        actions.append("coordinate_multi_seller_case")
    if split_payment and primary_issue != "valid_split_payment":
        actions.append("verify_payment_allocation")
    return actions


def build_case(case_id: str, facts: CaseFacts, confidence: float) -> dict:
    if not facts.order_found:
        return {
            "case_id": case_id,
            "case_assessment": {"primary_issue": "unsupported_late_claim", "secondary_issues": [],
                                 "case_status": "no_action", "confidence": 0.0},
            "affected_entities": {"order_ids": [], "item_ids": [], "seller_ids": [], "payment_ids": []},
            "customer_context": {"customer_unique_id": None, "related_order_ids": []},
            "product_context": {"product_ids": [], "category_names": []},
            "delivery_analysis": {"delivered_at": None, "estimated_delivery_at": None, "carrier_handoff_at": None,
                                   "delivery_variance_hours": None, "seller_handoff_analysis": [],
                                   "late_handoff_seller_ids": []},
            "payment_reconciliation": {"currency": "BRL", "item_total_brl": None, "freight_total_brl": None,
                                        "expected_total_brl": None, "payment_total_brl": None,
                                        "difference_brl": None, "reconciled": None, "payment_types": []},
            "root_cause_analysis": {"ranked_causes": [], "responsible_parties": []},
            "evidence_ids": [],
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0.0},
            "resolution_actions": [],
        }

    primary_issue, root_cause, responsible_parties, refund_amount, primary_action = classify(facts)
    secondary = secondary_issues(facts)
    case_status = "action_required" if primary_action in ("issue_full_refund", "refund_freight") else "no_action"
    actions = resolution_actions(primary_action, primary_issue, case_status, secondary, facts.split_payment)

    item_ids = cap([f"{facts.order_id}:{it['order_item_id']}" for it in facts.item_rows], "item_ids")
    payment_ids = cap([f"{facts.order_id}:{p['payment_sequential']}" for p in facts.payment_rows], "payment_ids")
    responsible_seller_ids = [rp["party_id"] for rp in responsible_parties if rp["party_type"] == "seller"]

    evidence_ids = [f"order:{facts.order_id}"]
    evidence_ids += [f"item:{iid}" for iid in item_ids]
    evidence_ids += [f"payment:{pid}" for pid in payment_ids]
    evidence_ids += [f"seller:{sid}" for sid in responsible_seller_ids]
    evidence_ids.append(f"policy:{root_cause}")
    evidence_ids = cap(evidence_ids, "evidence_ids")

    return {
        "case_id": case_id,
        "case_assessment": {
            "primary_issue": primary_issue,
            "secondary_issues": secondary,
            "case_status": case_status,
            "confidence": round2(min(1.0, max(0.0, confidence))),
        },
        "affected_entities": {
            "order_ids": cap([facts.order_id], "order_ids"),
            "item_ids": item_ids,
            "seller_ids": cap(facts.seller_ids, "seller_ids"),
            "payment_ids": payment_ids,
        },
        "customer_context": {
            "customer_unique_id": facts.customer_unique_id,
            "related_order_ids": cap(facts.related_order_ids, "related_order_ids"),
        },
        "product_context": {
            "product_ids": cap(facts.product_ids, "product_ids"),
            "category_names": cap(facts.category_names, "category_names"),
        },
        "delivery_analysis": {
            "delivered_at": facts.delivered_at,
            "estimated_delivery_at": facts.estimated_delivery_at,
            "carrier_handoff_at": facts.carrier_handoff_at,
            "delivery_variance_hours": facts.delivery_variance_hours,
            "seller_handoff_analysis": facts.seller_handoff_analysis,
            "late_handoff_seller_ids": facts.late_handoff_seller_ids,
        },
        "payment_reconciliation": {
            "currency": "BRL",
            "item_total_brl": facts.item_total_brl,
            "freight_total_brl": facts.freight_total_brl,
            "expected_total_brl": facts.expected_total_brl,
            "payment_total_brl": facts.payment_total_brl,
            "difference_brl": facts.difference_brl,
            "reconciled": facts.reconciled,
            "payment_types": facts.payment_types,
        },
        "root_cause_analysis": {
            "ranked_causes": cap([{"cause_code": root_cause, "rank": 1}], "root_causes"),
            "responsible_parties": cap(responsible_parties, "responsible_parties"),
        },
        "evidence_ids": evidence_ids,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": round2(refund_amount) if refund_amount else 0.0,
        },
        "resolution_actions": cap(actions, "resolution_actions"),
    }
