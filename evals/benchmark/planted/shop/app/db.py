import sqlite3

_conn = None


def connect(path="shop.db"):
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(path, check_same_thread=False)
    return _conn


def find_products(name_query):
    cur = connect().cursor()
    sql = f"SELECT id, name, price FROM products WHERE name LIKE '%{name_query}%'"
    return cur.execute(sql).fetchall()


def get_product(product_id):
    cur = connect().cursor()
    return cur.execute("SELECT id, name, price FROM products WHERE id = ?", (product_id,)).fetchone()


def add_order(user_id, items):
    conn = connect()
    total = 0
    for product_id, qty in items:
        row = get_product(product_id)
        total += row[2] * qty
    conn.execute("INSERT INTO orders (user_id, total) VALUES (?, ?)", (user_id, total))
    conn.commit()
    return total
