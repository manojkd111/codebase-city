import sqlite3
import threading

_local = threading.local()

# Columns a client may sort the invoice list by.
ALLOWED_SORT = {"created_at", "due_date", "total_cents", "number"}


def conn(path: str = "ledger.db") -> sqlite3.Connection:
    """One connection per thread."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(path)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def list_invoices(customer_id: int, sort: str = "created_at", descending: bool = False) -> list:
    if sort not in ALLOWED_SORT:
        raise ValueError(f"cannot sort by {sort!r}")
    direction = "DESC" if descending else "ASC"
    sql = f"SELECT * FROM invoices WHERE customer_id = ? ORDER BY {sort} {direction}"
    return conn().execute(sql, (customer_id,)).fetchall()


def get_invoice(invoice_id: int):
    from .invoices import Invoice

    row = conn().execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return Invoice.from_row(row) if row else None


def save_invoice(inv) -> None:
    conn().execute(
        "UPDATE invoices SET status = ?, total_cents = ? WHERE id = ?",
        (inv.status, inv.total_cents, inv.id),
    )
    conn().commit()
