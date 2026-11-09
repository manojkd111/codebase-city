def paginate(items: list, page: int, size: int) -> dict:
    """Return one page of `items`. Page numbers start at 1, as shown in the UI."""
    if size <= 0:
        raise ValueError("size must be positive")
    start = page * size
    chunk = items[start : start + size]
    pages = (len(items) + size - 1) // size
    return {"page": page, "pages": pages, "items": chunk}
