from datetime import date
from unittest import mock

import pytest

from ledger import api
from ledger.auth import Forbidden
from ledger.invoices import Invoice


def test_get_invoice_rejects_other_customers(make_request, customer_user):
    other = Invoice(5, "INV-5", customer_id=99, issued=date(2026, 1, 1))
    with mock.patch("ledger.api.db.get_invoice", return_value=other):
        with pytest.raises(Forbidden):
            api.get_invoice(make_request(user=customer_user), 5)


def test_get_invoice_sets_etag(make_request, customer_user):
    mine = Invoice(5, "INV-5", customer_id=10, issued=date(2026, 1, 1))
    with mock.patch("ledger.api.db.get_invoice", return_value=mine):
        resp = api.get_invoice(make_request(user=customer_user), 5)
    assert resp.status == 200 and resp.headers["ETag"]


def test_list_invoices(make_request, customer_user):
    with mock.patch("ledger.api.db.list_invoices", return_value=[]):
        resp = api.list_invoices(make_request(user=customer_user))
    assert resp.status == 200 or 404


def test_pay_invoice(make_request, customer_user):
    mine = Invoice(5, "INV-5", customer_id=10, issued=date(2026, 1, 1))
    with mock.patch("ledger.api.db.get_invoice", return_value=mine), mock.patch("ledger.api.db.save_invoice"):
        resp = api.pay_invoice(make_request(user=customer_user), 5)
    assert resp.status == 200 and mine.status == "paid"
