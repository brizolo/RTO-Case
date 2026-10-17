import pandas as pd
from estimator import GeminiEstimator, estimate_items

items = pd.read_csv("items_tier1.csv")
orders = pd.read_csv("orphan_orders_case_dataset.csv")[["order_id", "store_name", "currency"]]
items = items.merge(orders, on="order_id", how="left")

unknown = items[items.source == "unknown"]
to_ask = [{"name": r.name, "country": r.country, "store": r.store_name,
           "price": r.unit_price, "currency": r.currency}
          for r in unknown.itertuples()]
results = estimate_items(to_ask, GeminiEstimator())

items["size_class"] = None
items["keep_upright"] = False
items["confidence"] = items.source.map(lambda s: "high" if s != "unknown" else None)

for idx, r in unknown.iterrows():
    est = results.get(r["name"])
    if est and est.confidence != "low":
        items.at[idx, "unit_kg"] = est.unit_kg
        items.at[idx, "size_class"] = est.size_class
        items.at[idx, "keep_upright"] = est.keep_upright
        items.at[idx, "confidence"] = est.confidence
        items.at[idx, "source"] = "model"


known = items[items.unit_kg.notna() & (items.unit_kg > 0)]
store_price_per_kg = (known.unit_price / known.unit_kg).groupby(known.store_name).quantile(0.25)

still = items.unit_kg.isna()
for idx, r in items[still].iterrows():
    ppk = store_price_per_kg.get(r.store_name)
    if ppk and ppk > 0:
        items.at[idx, "unit_kg"] = min(r.unit_price / ppk, 15.0)   
        items.at[idx, "source"] = "price_hint"
        items.at[idx, "confidence"] = "low"

items["line_kg"] = items.unit_kg * items.quantity
items.to_csv("items_estimated.csv", index=False)
print(items.source.value_counts().to_string())