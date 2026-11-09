import csv
import io
import os
import subprocess
import tempfile

from .money import from_cents


def invoices_csv(rows) -> str:
    """CSV for the accounting team, opened in Excel."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["number", "customer", "total"])
    for r in rows:
        w.writerow([r["number"], r["customer_name"], from_cents(r["total_cents"])])
    return buf.getvalue()


def render_pdf(html: str, out_path: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "invoice.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)
        subprocess.run(["wkhtmltopdf", "--quiet", src, out_path], check=True, timeout=60)
