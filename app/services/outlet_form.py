import re
import json
from pathlib import Path

PRODUCT_NAMES = json.loads((Path(__file__).resolve().parents[1]/"data"/"report_products.json").read_text())
PRODUCT_KEYS = {name.casefold(): name for name in PRODUCT_NAMES}


def report_details(caption):
    fields = {}
    for line in (caption or "").splitlines():
        match = re.match(r"^\s*(Outlet Name|Outlet Type|ID|Dealer)(?:\s*[:：]\s*|\s+|(?=[\u1780-\u17ff]))(.+?)\s*$", line, re.I)
        if match:
            fields[match[1].lower()] = match[2].strip()
    return fields


def report_quantities(caption):
    quantities = []
    for line in (caption or "").splitlines():
        label, _, quantity = line.partition(":")
        product = PRODUCT_KEYS.get(label.strip().casefold())
        if product and quantity.strip():
            quantities.append(f"{product} × {quantity.strip()}")
    return quantities
