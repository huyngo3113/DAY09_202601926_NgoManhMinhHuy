"""Self-check for output/EC_*.json: exactly 50 files, required keys, array
caps, evidence-id format, and cross-consistency with affected_entities. Run
after run.py, before zipping output/ for submission.
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(ROOT, "output")

LIMITS = dict(order_ids=5, item_ids=5, seller_ids=3, payment_ids=5, related_order_ids=5,
              product_ids=5, category_names=5, root_causes=3, responsible_parties=3,
              evidence_ids=20, resolution_actions=5)

EVIDENCE_RE = re.compile(r"^(order:[^:]+|item:[^:]+:\d+|payment:[^:]+:\d+|seller:[^:]+|policy:[A-Z_]+)$")
REQUIRED_KEYS = ["case_id", "case_assessment", "affected_entities", "customer_context",
                  "product_context", "delivery_analysis", "payment_reconciliation",
                  "root_cause_analysis", "evidence_ids", "financial_resolution", "resolution_actions"]


def check_case(case_id, d, errors):
    for k in REQUIRED_KEYS:
        if k not in d:
            errors.append(f"{case_id}: missing key {k}")
    if d.get("case_id") != case_id:
        errors.append(f"{case_id}: case_id field mismatch ({d.get('case_id')!r})")

    ca = d.get("case_assessment", {})
    if ca.get("case_status") not in ("action_required", "no_action"):
        errors.append(f"{case_id}: bad case_status {ca.get('case_status')!r}")
    conf = ca.get("confidence")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        errors.append(f"{case_id}: confidence out of range {conf!r}")

    ae = d.get("affected_entities", {})
    for field, key in [("order_ids", "order_ids"), ("item_ids", "item_ids"), ("seller_ids", "seller_ids"),
                        ("payment_ids", "payment_ids")]:
        if len(ae.get(field, [])) > LIMITS[key]:
            errors.append(f"{case_id}: affected_entities.{field} exceeds cap {LIMITS[key]}")

    if len(d.get("customer_context", {}).get("related_order_ids", [])) > LIMITS["related_order_ids"]:
        errors.append(f"{case_id}: related_order_ids exceeds cap")
    if len(d.get("product_context", {}).get("product_ids", [])) > LIMITS["product_ids"]:
        errors.append(f"{case_id}: product_ids exceeds cap")
    if len(d.get("product_context", {}).get("category_names", [])) > LIMITS["category_names"]:
        errors.append(f"{case_id}: category_names exceeds cap")

    rca = d.get("root_cause_analysis", {})
    if len(rca.get("ranked_causes", [])) > LIMITS["root_causes"]:
        errors.append(f"{case_id}: ranked_causes exceeds cap")
    if len(rca.get("responsible_parties", [])) > LIMITS["responsible_parties"]:
        errors.append(f"{case_id}: responsible_parties exceeds cap")

    evidence = d.get("evidence_ids", [])
    if len(evidence) > LIMITS["evidence_ids"]:
        errors.append(f"{case_id}: evidence_ids exceeds cap")
    for eid in evidence:
        if not EVIDENCE_RE.match(eid):
            errors.append(f"{case_id}: malformed evidence id {eid!r}")

    if len(d.get("resolution_actions", [])) > LIMITS["resolution_actions"]:
        errors.append(f"{case_id}: resolution_actions exceeds cap")

    # cross-consistency: every order/item/payment evidence id must point at an order_id
    # that is actually this case's claimed order (no cross-case leakage).
    order_ids = set(ae.get("order_ids", []))
    for eid in evidence:
        if eid.startswith("order:") and eid.split(":", 1)[1] not in order_ids:
            errors.append(f"{case_id}: evidence {eid!r} not in affected_entities.order_ids")
        if eid.startswith("item:"):
            oid = eid.split(":")[1]
            if oid not in order_ids:
                errors.append(f"{case_id}: evidence {eid!r} references unrelated order")


def main():
    files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "EC_*.json")))
    expected = [f"EC_{i:03d}" for i in range(1, 51)]
    errors = []

    found_ids = [os.path.splitext(os.path.basename(f))[0] for f in files]
    if found_ids != expected:
        missing = set(expected) - set(found_ids)
        extra = set(found_ids) - set(expected)
        if missing:
            errors.append(f"missing output files: {sorted(missing)}")
        if extra:
            errors.append(f"unexpected output files: {sorted(extra)}")

    for f in files:
        case_id = os.path.splitext(os.path.basename(f))[0]
        try:
            d = json.load(open(f, encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"{case_id}: invalid JSON ({e})")
            continue
        check_case(case_id, d, errors)

    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"PASS: {len(files)} output files, schema/limits/evidence-format all OK")


if __name__ == "__main__":
    main()
