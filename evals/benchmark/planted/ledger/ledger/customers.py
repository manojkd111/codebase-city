from dataclasses import dataclass


@dataclass
class Customer:
    id: int
    name: str
    email: str
    region: str  # ISO 3166 country code, stored upper-case: "DE", "FR"


def normalize_region(region: str) -> str:
    return region.strip().upper()


def create(db, name: str, email: str, region: str) -> Customer:
    region = normalize_region(region)
    cur = db.execute(
        "INSERT INTO customers (name, email, region) VALUES (?, ?, ?)",
        (name, email, region),
    )
    db.commit()
    return Customer(cur.lastrowid, name, email, region)
