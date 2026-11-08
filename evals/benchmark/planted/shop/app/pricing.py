BULK_THRESHOLD = 10


def apply_discount(total, code=None, item_count=0, member=False):
    discount = 0.0
    if code:
        code = code.strip().upper()
        if code == "WELCOME10":
            discount += 0.10
        elif code == "VIP25":
            if member:
                discount += 0.25
            else:
                discount += 0.05
        elif code.startswith("FLASH"):
            try:
                pct = int(code[5:])
            except ValueError:
                pct = 0
            if 0 < pct <= 50:
                discount += pct / 100
    if item_count > BULK_THRESHOLD:
        discount += 0.05
    if member and total > 100:
        discount += 0.02
    if discount > 0.5:
        discount = 0.5
    return round(total * (1 - discount), 2)


def shipping(total, country):
    if country == "US":
        if total >= 50:
            return 0
        return 5
    elif country in ("CA", "MX"):
        return 10
    else:
        return 25
