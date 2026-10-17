import json
import sys
import pandas as pd
from parser import parse_unit_weight

DATA = sys.argv[1] if len(sys.argv) > 1 else "orphan_orders_case_dataset.csv"
orders = pd.read_csv(DATA)

rows = []
for o in orders.itertuples():
    for item in json.loads(o.products_json):
        name_kg, matched = parse_unit_weight(item["name"])
        declared = item.get("declared_weight_kg")

        if name_kg is not None and declared is not None:
            unit_kg = name_kg
            source = "name (declared ignored)"
            conflict = max(name_kg, declared) > 2 * min(name_kg, declared)
        elif name_kg is not None:
            unit_kg, source, conflict = name_kg, "name", False
        elif declared is not None:
            unit_kg, source, conflict = declared, "declared", False
        else:
            unit_kg, source, conflict = None, "unknown", False

        rows.append({
            "order_id": o.order_id, "country": o.country, "vertical": o.vertical,
            "name": item["name"], "quantity": item["quantity"],
            "unit_price": item["unit_price"], "matched_text": matched,
            "unit_kg": unit_kg,
            "line_kg": None if unit_kg is None else unit_kg * item["quantity"],
            "source": source, "declared_conflict": conflict,
        })

items = pd.DataFrame(rows)
items.to_csv("items_tier1.csv", index=False)

per_order = items.groupby("order_id").agg(
    lines=("name", "size"),
    unknown_lines=("source", lambda s: (s == "unknown").sum()),
    known_kg=("line_kg", "sum"),
    max_unit_kg=("unit_kg", "max"),
).reset_index()
per_order.to_csv("orders_tier1.csv", index=False)

items["known"] = items["source"] != "unknown"
print(f"Item lines: {len(items)}   with a weight: {items.known.mean():.0%}")
print("\nBy source:\n", items.source.value_counts().to_string())
print("\nShare of lines with a weight, by vertical:")
print(items.groupby("vertical").known.mean().round(2).to_string())
print("\nShare of lines with a weight, by country:")
print(items.groupby("country").known.mean().sort_values().round(2).to_string())
fully = (per_order.unknown_lines == 0).mean()
print(f"\nOrders where EVERY line has a weight: {fully:.0%}")
print(f"Declared-weight conflicts: {items.declared_conflict.sum()}")