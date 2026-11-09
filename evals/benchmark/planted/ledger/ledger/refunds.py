from dataclasses import dataclass
from datetime import date, datetime


class RefundError(Exception):
    pass


@dataclass
class Payment:
    id: int
    amount_cents: int
    refunded_cents: int
    paid_at: datetime


REFUND_WINDOW_DAYS = 30
DEFECT_WINDOW_DAYS = 60


def calculate_refund(invoice, payments: list[Payment], requested_cents: int | None, reason: str, today: date):
    """How much to refund, split across the payments made (newest first).

    Returns a list of (payment_id, cents). `requested_cents=None` refunds everything still refundable.
    Chargebacks ignore the refund window; defective goods get a longer one.
    """
    if invoice.status not in ("paid", "partially_refunded"):
        raise RefundError("invoice is not paid")
    refundable = sum(p.amount_cents - p.refunded_cents for p in payments)
    if requested_cents is None:
        requested_cents = refundable
    if requested_cents <= 0 or requested_cents > refundable:
        raise RefundError(f"can refund between 1 and {refundable} cents")
    if reason != "chargeback":
        window = DEFECT_WINDOW_DAYS if reason == "defective" else REFUND_WINDOW_DAYS
        if invoice.paid_on is None or (today - invoice.paid_on).days > window:
            raise RefundError("refund window closed")
    plan = []
    remaining = requested_cents
    for p in sorted(payments, key=lambda p: p.paid_at, reverse=True):
        available = p.amount_cents - p.refunded_cents
        if available <= 0:
            continue
        take = min(available, remaining)
        plan.append((p.id, take))
        remaining -= take
        if remaining == 0:
            break
    return plan


def new_status(invoice, plan, payments: list[Payment]) -> str:
    refunded = sum(c for _, c in plan) + sum(p.refunded_cents for p in payments)
    paid = sum(p.amount_cents for p in payments)
    return "refunded" if refunded >= paid else "partially_refunded"
