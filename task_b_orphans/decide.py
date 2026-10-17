import json
import math
import re
import pandas as pd

CFG = json.load(open("limits.json"))
VEHICLES = CFG["vehicles"]                      
CAR = VEHICLES[-1]

AGE_RESTRICTED = re.compile(
    r"tobacco|tutun|cigaret|ţigar|\bdunhill|\bmarlboro|\bkent\b|\bterea\b|\biqos|\bheets\b|"
    r"vodka|wódka|водк|\bwhiske?y\b|\bbeer\b|\bpiwo\b|\bпиво\b|\bcerveja\b|\bbere\b|"
    r"\bwine\b|\bvinho\b|\bvin\b|\bwino\b|\bgin\b|\brum\b",
    re.IGNORECASE)

def item_litres(row):
    """Approximate space one line takes, in litres."""
    if isinstance(row.size_class, str):                       
        return CFG["size_class_litres"][row.size_class] * row.quantity
    return row.line_kg * CFG["litres_per_kg_groceries"]        


def smallest_vehicle(kg, litres, max_item_kg, has_bulky):
    for v in VEHICLES:
        if has_bulky and v is not CAR:
            continue
        if kg <= v["optimistic_kg"] and litres <= v["space_l"] and max_item_kg <= v["max_item_kg"]:
            return v
    return None


def decide(order, lines, cash_threshold):
    flags, notes, reasons = [], [], []  
    lines = lines.copy()
    no_weight = lines.unit_kg.isna()
    lines.loc[no_weight, "unit_kg"] = CFG["unknown_item_kg"] 
    lines["line_kg"] = lines.unit_kg * lines.quantity
    lines["litres"] = lines.apply(item_litres, axis=1)

    kg, litres = lines.line_kg.sum(), lines.litres.sum()
    max_item = lines.unit_kg.max()
    has_bulky = (lines.size_class == "bulky").any()
    guessed = lines[lines.source.isin(["price_hint", "unknown"]) | no_weight].line_kg.sum()
    guessed_share = guessed / kg if kg else 1.0

    named = lines[lines.source.str.startswith("name")]
    reasons.append(f"{kg:.1f} kg / ~{litres:.0f} L; "
                   f"{len(named)}/{len(lines)} lines weighed from item names")

    if max_item > CFG["carry_limit_kg"]:
        heavy = lines.loc[lines.unit_kg.idxmax(), "name"]
        flags.append("heavy item")
        reasons.append(f"'{heavy[:40]}' is {max_item:.0f} kg, over the {CFG['carry_limit_kg']} kg carry limit")
    if lines.keep_upright.fillna(False).astype(bool).any():
        notes.append("keep upright")      
    if order.order_total == 0:
        flags.append("zero total")
    if lines.name.str.contains(AGE_RESTRICTED).any():
        flags.append("age-restricted (ID check)")
    if order.partnership != "GEN2_PARTNERS":
        flags.append("unusual partner type")
    if (lines.quantity >= CFG["abnormal_quantity"]).any():
        flags.append("abnormal quantity")
    if order.payment_method == "CASH" and order.products_total >= cash_threshold:
        flags.append("high-value cash")

    if max_item > CFG["carry_limit_kg"]:
        decision = "call customer"
    else:
        v = smallest_vehicle(kg, litres, max_item, has_bulky)
        if v:
            decision = v["name"]
            if kg > v["conservative_kg"]:
                reasons.append(f"{kg:.1f} kg is between {v['name']}'s safe "
                               f"{v['conservative_kg']} kg and max {v['optimistic_kg']} kg")
        else:

            n_cars = max(math.floor(kg / CAR["optimistic_kg"]), math.floor(litres / CAR["space_l"]), 1)
            rest_kg = max(kg - n_cars * CAR["optimistic_kg"], 0)
            rest_l = max(litres - n_cars * CAR["space_l"], 0)
            rest = smallest_vehicle(rest_kg, rest_l, min(max_item, rest_kg), False) if rest_kg else None
            parts = ["car"] * n_cars + ([rest["name"]] if rest else [])
            decision = f"split: {' + '.join(parts)}"
            reasons.append(f"over one car's {CAR['optimistic_kg']} kg / {CAR['space_l']} L")

    auto_ok = (
        not flags
        and not decision.startswith(("split", "call"))
        and guessed_share <= CFG["max_guessed_share"]
        and kg <= next(v for v in VEHICLES if v["name"] == decision)["conservative_kg"]
    )
    if guessed_share > CFG["max_guessed_share"]:
        reasons.append(f"{guessed_share:.0%} of the weight is guessed")

    return {
        "order_id": order.order_id, "country": order.country, "vertical": order.vertical,
        "decision": decision, "needs_human": not auto_ok,
        "est_kg": round(kg, 1), "est_litres": round(litres), "max_item_kg": round(max_item, 1),
        "guessed_share": round(guessed_share, 2), "flags": ", ".join(flags),
        "handling_notes": ", ".join(notes),
        "reason": "; ".join(reasons),
    }


if __name__ == "__main__":
    orders = pd.read_csv("orphan_orders_case_dataset.csv")
    items = pd.read_csv("items_estimated.csv")
    cash_thr = orders.groupby("currency").products_total.quantile(CFG["high_value_cash_percentile"])

    out = [decide(o, items[items.order_id == o.order_id], cash_thr[o.currency])
           for o in orders.itertuples()]
    d = pd.DataFrame(out)
    d.to_csv("decisions.csv", index=False)

    print(d.decision.value_counts().to_string())
    print(f"\nDecided automatically: {(~d.needs_human).mean():.0%}")
    print(f"Orders with a handling note only: {(d.handling_notes.fillna('') != '').sum()}")
    print("\nReview flags:\n", d["flags"].str.split(", ").explode().replace("", pd.NA).dropna().value_counts().to_string())