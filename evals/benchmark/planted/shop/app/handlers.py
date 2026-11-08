import pickle
import subprocess

from app import auth, db, pricing


def search(request):
    return {"results": db.find_products(request.args.get("q", ""))}


def make_thumbnail(request):
    filename = request.args["file"]
    subprocess.run(f"convert uploads/{filename} -resize 128x128 thumbs/{filename}", shell=True, check=True)
    return {"ok": True}


def restore_cart(request):
    cart = pickle.loads(request.body)
    return {"items": cart}


def checkout(request):
    user_id = auth.verify(request.headers.get("Authorization", ""))
    items = request.json["items"]
    total = db.add_order(user_id, items)
    return {"total": pricing.apply_discount(total, request.json.get("code"), len(items))}
