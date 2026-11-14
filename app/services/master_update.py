from app.services.outlet_form import PRODUCT_NAMES

from app.data.sa_members import MASTER_ASSIGNMENTS, normalize_group, normalize_username
from app.database.models import AppliedMaster, Product, SAList, SAUser


def apply_master(db):
    version = "2026-11-14"
    if db.get(AppliedMaster, version):
        return
    existing = {(normalize_group(r.group_name), r.telegram_user_id): r for r in db.query(SAList)}
    runtime = {(normalize_group(r.group_name), r.telegram_user_id): r for r in db.query(SAUser)}
    for m in MASTER_ASSIGNMENTS:
        key = normalize_group(m["group_name"]), m["telegram_user_id"]
        user = runtime.get(key)
        if user:
            user.is_from_list = m["is_active"]
            user.source_type = "excel"
            user.working_time_override = None
            user.working_time = m["working_time"]
            user.position = m["position"]
        row = existing.get(key)
        if row:
            for field in ("name", "region", "dealer", "group_name", "position", "working_time", "is_active"):
                setattr(row, field, m[field])
            row.username = normalize_username(m["username"])
            row.source_type = "excel"
    products = {p.name.casefold() for p in db.query(Product)}
    skus = {p.sku for p in db.query(Product)}
    for name in PRODUCT_NAMES:
        sku = name.replace(" ", "-")
        if name.casefold() not in products and sku not in skus:
            db.add(Product(name=name, sku=sku, category="Beverages"))
    db.add(AppliedMaster(version=version))
    db.commit()
