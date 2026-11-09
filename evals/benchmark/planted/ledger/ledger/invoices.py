import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import tax
from .money import percent_of

PAYMENT_TERMS_DAYS = 30
LATE_FEE_PCT = Decimal("1.5")


@dataclass
class Line:
    description: str
    quantity: int
    unit_cents: int


@dataclass
class Invoice:
    id: int | None
    number: str
    customer_id: int
    issued: date
    lines: list[Line] = field(default_factory=list)
    total_cents: int = 0
    status: str = "open"  # open | paid | partially_refunded | refunded | void
    late_fee_applied: bool = False
    paid_on: date | None = None

    @property
    def due(self) -> date:
        return self.issued + timedelta(days=PAYMENT_TERMS_DAYS)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_row(cls, row) -> "Invoice":
        return cls(
            id=row["id"],
            number=row["number"],
            customer_id=row["customer_id"],
            issued=date.fromisoformat(row["issued"]),
            total_cents=row["total_cents"],
            status=row["status"],
        )


def build(customer, lines: list[Line], issued: date, number: str) -> Invoice:
    net = sum(line.quantity * line.unit_cents for line in lines)
    total = net + tax.tax_cents(net, customer.region)
    return Invoice(None, number, customer.id, issued, lines, total)


def apply_late_fee(invoice: Invoice, today: date) -> None:
    """Add the late fee once an invoice is overdue. The nightly job calls this for every open invoice."""
    if invoice.status != "open" or today <= invoice.due:
        return
    fee = percent_of(invoice.total_cents, LATE_FEE_PCT)
    invoice.total_cents += fee


def mark_paid(invoice: Invoice, today: date) -> None:
    if invoice.status != "open":
        raise ValueError(f"invoice {invoice.number} is {invoice.status}")
    invoice.status = "paid"
    invoice.paid_on = today
