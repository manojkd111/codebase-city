import hashlib
from dataclasses import dataclass, field
from datetime import date

from . import auth, db, invoices, refunds
from .pagination import paginate


@dataclass
class Response:
    status: int
    body: object = None
    headers: dict = field(default_factory=dict)


def _owned(invoice, user):
    if invoice is None or invoice.customer_id != user.customer_id:
        raise auth.Forbidden("not your invoice")
    return invoice


def get_invoice(request, invoice_id: int) -> Response:
    auth.require_login(request.user)
    inv = _owned(db.get_invoice(invoice_id), request.user)
    body = inv.to_json()
    # Cache validator for conditional GETs; not a security control.
    etag = hashlib.md5(body.encode(), usedforsecurity=False).hexdigest()
    return Response(200, body, {"ETag": etag})


def list_invoices(request) -> Response:
    auth.require_login(request.user)
    rows = db.list_invoices(request.user.customer_id, request.args.get("sort", "created_at"))
    page = int(request.args.get("page", 1))
    return Response(200, paginate(rows, page, 20))


def pay_invoice(request, invoice_id: int) -> Response:
    auth.require_login(request.user)
    inv = _owned(db.get_invoice(invoice_id), request.user)
    invoices.mark_paid(inv, date.today())
    db.save_invoice(inv)
    return Response(200, inv.to_json())


def void_invoice(request, invoice_id: int) -> Response:
    auth.require_login(request.user)
    inv = db.get_invoice(invoice_id)
    if inv is None or inv.status != "open":
        return Response(409, "only open invoices can be voided")
    inv.status = "void"
    db.save_invoice(inv)
    return Response(204)


def refund_invoice(request, invoice_id: int) -> Response:
    auth.require_role(request.user, "billing")
    inv = db.get_invoice(invoice_id)
    payments = request.payments_for(invoice_id)
    plan = refunds.calculate_refund(
        inv, payments, request.json.get("cents"), request.json.get("reason", "other"), date.today()
    )
    inv.status = refunds.new_status(inv, plan, payments)
    db.save_invoice(inv)
    return Response(200, {"plan": plan})
