from datetime import date, timedelta
from unittest import mock

from ledger.customers import Customer
from ledger.invoices import Invoice, Line, apply_late_fee, build, mark_paid


def test_build_adds_tax():
    customer = Customer(1, "Acme GmbH", "billing@acme.example", "DE")
    with mock.patch("ledger.invoices.tax.tax_cents", return_value=190) as tax_cents:
        build(customer, [Line("widget", 1, 1000)], date(2026, 1, 1), "INV-2026-000001")
    tax_cents.assert_called_once()


def test_due_date():
    inv = Invoice(1, "INV-1", 1, date(2026, 1, 1))
    assert inv.due == date(2026, 1, 31)


def test_late_fee_after_due_date():
    inv = Invoice(1, "INV-1", 1, date(2026, 1, 1), total_cents=10000)
    apply_late_fee(inv, inv.due + timedelta(days=1))
    assert inv.total_cents == 10150


def test_no_late_fee_before_due_date():
    inv = Invoice(1, "INV-1", 1, date(2026, 1, 1), total_cents=10000)
    apply_late_fee(inv, inv.due)
    assert inv.total_cents == 10000


def test_mark_paid():
    inv = Invoice(1, "INV-1", 1, date(2026, 1, 1))
    mark_paid(inv, date(2026, 1, 5))
    assert inv.status == "paid" and inv.paid_on == date(2026, 1, 5)
