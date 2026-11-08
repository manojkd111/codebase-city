from app import db


def test_find_products_runs():
    db.find_products("lamp")


def test_get_product_missing():
    try:
        db.get_product(-1)
    except Exception:
        pass
