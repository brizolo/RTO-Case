"""
Agent screen for orphan validation.

Run:   streamlit run app.py
Needs: decisions.csv, items_estimated.csv, orphan_orders_case_dataset.csv, limits.json
Saves: agent_actions.db (SQLite) - every confirm/override, and every item
       weight an agent corrects (these corrections feed the item cache).
"""
import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from estimator import DEFAULT_INSTRUCTIONS, PROMPT_FILE, instructions

st.set_page_config(page_title="Orphan validation", layout="wide")

# Logo: put the official file next to app.py as logo.png (not included in the repo).
import os
if os.path.exists("logo.png") and hasattr(st, "logo"):
    st.logo("logo.png", size="large")

YELLOW, GREEN, INK, MUTED = "#FFC244", "#00A082", "#1D1D1B", "#6B6B66"
st.markdown(f"""
<style>
  .block-container {{padding-top: 2rem; max-width: 1200px;}}
  h1, h2, h3 {{letter-spacing: -0.01em;}}
  .card {{border:1px solid #E8E8E4; border-radius:14px; padding:20px 24px; background:#fff;}}
  .eyebrow {{font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:{MUTED};}}
  .order-id {{font-size:30px; font-weight:700; color:{INK}; margin:2px 0 4px;}}
  .meta {{font-size:14px; color:{MUTED};}}
  .decision {{display:inline-block; font-size:26px; font-weight:700; padding:6px 18px;
              border-radius:999px; background:{YELLOW}; color:{INK};}}
  .decision.auto {{background:{GREEN}; color:#fff;}}
  .decision.stop {{background:{INK}; color:{YELLOW};}}
  .kpis {{display:flex; gap:36px; margin-top:16px; flex-wrap:wrap;}}
  .kpi b {{display:block; font-size:22px; color:{INK};}}
  .kpi span {{font-size:12px; color:{MUTED}; text-transform:uppercase; letter-spacing:.06em;}}
  .chip {{display:inline-block; font-size:13px; padding:3px 10px; border-radius:999px;
          margin:8px 6px 0 0;}}
  .chip.flag {{background:#FDE2DF; color:#9B1C12;}}
  .chip.note {{background:#E3F1FB; color:#0B4F7C;}}
  .chip.ok   {{background:#DDF3EC; color:#065F4B;}}
  .why {{margin-top:14px; font-size:15px; color:{INK};}}
  .proto {{font-size:11px; color:{MUTED};}}
</style>""", unsafe_allow_html=True)
DB = "agent_actions.db"
LIMITS = json.load(open("limits.json"))
VEHICLE_OPTIONS = ["bike", "e-bike", "motorbike", "car", "split", "call customer"]
OVERRIDE_REASONS = ["weight estimate wrong", "item size wrong", "item not recognised",
                    "customer contacted", "flag not relevant", "other"]


# ---------- capacity bars: where this order sits against each vehicle ----------
def capacity_bars(kg, litres, recommended):
    """One bar per vehicle: green = safe (conservative), amber = needs a check
    (up to optimistic), red = over the limit. The black line is this order."""
    rows = []
    for v in LIMITS["vehicles"]:
        top = max(v["optimistic_kg"] * 1.3, kg * 1.05)          # scale of this bar
        safe = v["conservative_kg"] / top * 100
        maxi = v["optimistic_kg"] / top * 100
        mark = min(kg / top * 100, 99.5)
        space_ok = litres <= v["space_l"]
        chosen = recommended == v["name"]
        label = f"<b>{v['name']}</b> ◀" if chosen else v["name"]
        note = "" if space_ok else f" · space {litres:.0f}/{v['space_l']} L ✕"
        rows.append(f"""
        <div style="display:flex;align-items:center;gap:12px;margin:6px 0;">
          <div style="width:110px;font-size:14px;">{label}</div>
          <div style="position:relative;flex:1;height:18px;border-radius:4px;overflow:hidden;
                      background:linear-gradient(to right,
                        #CDEBD8 0 {safe}%, #FFE7A3 {safe}% {maxi}%, #F6C7C3 {maxi}% 100%);">
            <div style="position:absolute;left:{mark}%;top:0;bottom:0;width:3px;background:#1D1D1B;"></div>
          </div>
          <div style="width:220px;font-size:12px;color:#5B5B57;">
            safe {v['conservative_kg']} · max {v['optimistic_kg']} kg{note}</div>
        </div>""")
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption(f"Black line = this order ({kg} kg). Green: decided automatically · "
               "Amber: a person checks · Red: over the limit.")


# ---------- data ----------
@st.cache_data
def load():
    d = pd.read_csv("decisions.csv")
    items = pd.read_csv("items_estimated.csv")
    orders = pd.read_csv("orphan_orders_case_dataset.csv")
    d = d.merge(orders[["order_id", "store_name", "payment_method", "currency",
                        "products_total", "order_total"]], on="order_id", how="left")
    for col in ("flags", "handling_notes", "reason"):
        d[col] = d[col].fillna("")
    # Queue order (her choice): orders needing a human first, riskiest first.
    # Note: r["flags"], not r.flags - pandas rows already have a .flags attribute.
    def risk(r):
        if not r["needs_human"]:              return 5
        if r["decision"] == "call customer":  return 0
        if r["decision"].startswith("split"): return 1
        if r["flags"]:                        return 2
        if r["guessed_share"] > 0.30:         return 3
        return 4                               # above conservative limit only
    d["risk"] = d.apply(risk, axis=1)
    d = d.sort_values(["risk", "est_kg"], ascending=[True, False]).reset_index(drop=True)
    return d, items


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS actions(
        ts TEXT, agent TEXT, order_id INTEGER, engine_decision TEXT,
        action TEXT, final_decision TEXT, reason TEXT, note TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS item_corrections(
        ts TEXT, agent TEXT, order_id INTEGER, item_name TEXT,
        old_kg REAL, new_kg REAL)""")
    return con


# ---------- courier availability (no dispatch feed in the prototype: set by hand) ----------
VEHICLES = [v["name"] for v in LIMITS["vehicles"]]
DEFAULT_AVAILABLE = {"bike": 20, "e-bike": 10, "motorbike": 8, "car": 3}


def availability(country):
    """Latest count per vehicle for a country; defaults until someone sets it."""
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS availability(
            ts TEXT, agent TEXT, country TEXT, vehicle TEXT, available INTEGER)""")
        rows = con.execute("""SELECT vehicle, available FROM availability a
            WHERE country = ? AND ts = (SELECT MAX(ts) FROM availability b
                WHERE b.country = a.country AND b.vehicle = a.vehicle)""", (country,)).fetchall()
    out = dict(DEFAULT_AVAILABLE)
    out.update({v: n for v, n in rows})
    return out


def save_availability(country, counts, who):
    ts = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.executemany("INSERT INTO availability VALUES (?,?,?,?,?)",
                        [(ts, who, country, v, int(n)) for v, n in counts.items()])


def vehicles_needed(decision):
    """'split: car + bike' -> ['car', 'bike']; 'call customer' -> []."""
    d = decision.replace("split:", "")
    return [p.strip() for p in d.split("+") if p.strip() in VEHICLES]


def fits(v, kg, litres, max_item):
    return (kg <= v["optimistic_kg"] and litres <= v["space_l"]
            and max_item <= v["max_item_kg"])


def availability_advice(order, avail):
    """If the recommended vehicle has nobody free, suggest the next vehicle that
    fits and is free, or say the order has to wait, be split or be scheduled."""
    needed = vehicles_needed(order.decision)
    short = [v for v in needed if avail.get(v, 0) <= 0]
    if not needed or not short:
        return None
    if len(needed) == 1:
        start = VEHICLES.index(needed[0])
        for v in LIMITS["vehicles"][start + 1:]:
            if avail.get(v["name"], 0) > 0 and fits(v, order.est_kg, order.est_litres, order.max_item_kg):
                return f"No {needed[0]} free in {order.country} now: a {v['name']} could take it"
    return f"No {', '.join(short)} free in {order.country} now: wait, split or schedule"


def reviewed_ids():
    with db() as con:
        return {r[0] for r in con.execute("SELECT DISTINCT order_id FROM actions")}


decisions, items = load()
done = reviewed_ids()
RISK_LABEL = {0: "🔴 call customer", 1: "🟠 split", 2: "🟡 flagged",
              3: "⚪ mostly guessed", 4: "⚪ near limit", 5: "🟢 auto"}

# ---------- sidebar: queue ----------
with st.sidebar:
    st.header("Queue")
    agent = st.text_input("Your name", value="agent-1")
    only_human = st.toggle("Only orders needing a human", value=True)
    hide_done = st.toggle("Hide orders I've handled", value=True)
    countries = st.multiselect("Country", sorted(decisions.country.unique()))

    # ---------- model instructions: editable by the team, every version logged ----------
    with st.expander("Model instructions"):
        st.caption("What the language model is told when it estimates items with no size in "
                   "the name. The answer format is fixed in code. Changes apply to products "
                   "the model has not seen yet; every version is saved with your name.")
        current = instructions()
        text = st.text_area("Instructions", current, height=220, label_visibility="collapsed")
        why = st.text_input("Why are you changing it?", key="prompt_why")
        c1, c2 = st.columns(2)
        if c1.button("Save new version", disabled=(text.strip() == current.strip() or not why)):
            with open(PROMPT_FILE, "w", encoding="utf-8") as f:
                f.write(text.strip())
            with db() as con:
                con.execute("""CREATE TABLE IF NOT EXISTS prompt_versions(
                    ts TEXT, agent TEXT, reason TEXT, text TEXT)""")
                con.execute("INSERT INTO prompt_versions VALUES (?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), agent, why, text.strip()))
            st.success("Saved. New products will use it.")
        if c2.button("Restore default", disabled=(current.strip() == DEFAULT_INSTRUCTIONS.strip())):
            if os.path.exists(PROMPT_FILE):
                os.remove(PROMPT_FILE)
            with db() as con:
                con.execute("""CREATE TABLE IF NOT EXISTS prompt_versions(
                    ts TEXT, agent TEXT, reason TEXT, text TEXT)""")
                con.execute("INSERT INTO prompt_versions VALUES (?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), agent, "restored default",
                             DEFAULT_INSTRUCTIONS))
            st.rerun()
        with db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS prompt_versions(
                ts TEXT, agent TEXT, reason TEXT, text TEXT)""")
            hist = pd.read_sql("SELECT ts, agent, reason FROM prompt_versions "
                               "ORDER BY ts DESC LIMIT 5", con)
        if not hist.empty:
            st.dataframe(hist, hide_index=True, width="stretch")

    q = decisions
    if only_human:
        q = q[q.needs_human]
    if hide_done:
        q = q[~q.order_id.isin(done)]
    if countries:
        q = q[q.country.isin(countries)]

    st.caption(f"{len(q)} in queue · {len(done)} handled · "
               f"{(~decisions.needs_human).mean():.0%} of all orders decided automatically")
    if q.empty:
        st.success("Queue empty.")
        st.stop()

    labels = [f"{RISK_LABEL[r.risk]} · {r.order_id} · {r.country} · {r.decision}"
              for r in q.itertuples()]
    if "pos" not in st.session_state or st.session_state.pos >= len(labels):
        st.session_state.pos = 0
    pick = st.radio("Orders", labels, index=st.session_state.pos, label_visibility="collapsed")
    st.session_state.pos = labels.index(pick)

order = q.iloc[st.session_state.pos]
lines = items[items.order_id == order.order_id]

# ---------- main: the order ----------
if order.decision == "call customer" or order.decision.startswith("split"):
    pill = "stop"
elif not order.needs_human:
    pill = "auto"
else:
    pill = ""
chips = "".join(f'<span class="chip flag">{f}</span>' for f in order["flags"].split(", ") if f)
chips += "".join(f'<span class="chip note">courier: {n}</span>'
                 for n in order.handling_notes.split(", ") if n)
avail = availability(order.country)
advice = availability_advice(order, avail)
if advice:
    chips += f'<span class="chip flag">{advice}</span>'
if not chips:
    chips = '<span class="chip ok">no flags</span>'
status = "Decided automatically" if not order.needs_human else "Needs your check"

st.markdown(f"""
<div class="card">
  <div class="eyebrow">{status} · {RISK_LABEL[order.risk]}</div>
  <div class="order-id">Order {order.order_id}</div>
  <div class="meta">{order.country} · {order.vertical.replace("_", "-").lower()} · {order.store_name} ·
       {order.products_total:,.2f} {order.currency} · {order.payment_method.replace("_", " ").lower()}</div>
  <div style="margin-top:16px;"><span class="decision {pill}">{order.decision}</span></div>
  <div class="kpis">
    <div class="kpi"><b>{order.est_kg} kg</b><span>estimated weight</span></div>
    <div class="kpi"><b>{order.est_litres} L</b><span>estimated space</span></div>
    <div class="kpi"><b>{order.max_item_kg} kg</b><span>heaviest item</span></div>
    <div class="kpi"><b>{order.guessed_share:.0%}</b><span>weight guessed</span></div>
  </div>
  <div>{chips}</div>
  <div class="why"><b>Why:</b> {order.reason}</div>
</div>""", unsafe_allow_html=True)

st.subheader("Where this order sits")
capacity_bars(order.est_kg, order.est_litres, order.decision)

st.subheader(f"Couriers available in {order.country}")
pending = decisions[(decisions.country == order.country) & decisions.needs_human
                    & ~decisions.order_id.isin(done)]
need = {v: 0 for v in VEHICLES}
for dec in pending.decision:
    for v in vehicles_needed(dec):
        need[v] += 1
with st.form(f"avail-{order.country}"):
    cols = st.columns(len(VEHICLES))
    counts = {}
    for col, v in zip(cols, VEHICLES):
        counts[v] = col.number_input(v, min_value=0, step=1, value=int(avail[v]),
                                     key=f"av-{order.country}-{v}")
        gap = counts[v] - need[v]
        col.caption(f"{need[v]} open orders need one · "
                    + ("enough" if gap >= 0 else f"short by {-gap}"))
    if st.form_submit_button("Update availability"):
        save_availability(order.country, counts, agent)
        st.rerun()
st.caption("No dispatch feed in the prototype: a shift lead or agent sets these. "
           "In production they would come from the dispatch system.")

st.subheader("Items")
SOURCE_LABEL = {"name": "read from name", "name (declared ignored)": "read from name",
                "declared": "partner catalogue", "model": "AI estimate",
                "price_hint": "guessed from price", "unknown": "unknown"}
view = pd.DataFrame({
    "qty": lines.quantity,
    "item": lines.name,
    "kg each": lines.unit_kg.round(3),
    "kg total": lines.line_kg.round(2),
    "size": lines.size_class.fillna("–"),
    "where the weight comes from": lines.source.map(SOURCE_LABEL).fillna(lines.source),
}).sort_values("kg total", ascending=False)
UNCERTAIN = {"AI estimate": "#FFF4D1", "guessed from price": "#FFE0B2", "unknown": "#F6C7C3"}


def shade(row):
    colour = UNCERTAIN.get(row["where the weight comes from"], "")
    return [f"background-color: {colour}" if colour else ""] * len(row)


st.dataframe(view.style.apply(shade, axis=1).format({"kg each": "{:.3f}", "kg total": "{:.2f}"}),
             hide_index=True, width="stretch")
st.caption("Shaded rows are estimates, not read from the product: check these first.")

# ---------- actions ----------
st.subheader("Your decision")
left, right = st.columns(2)

with left.form(f"decide-{order.order_id}"):
    action = st.radio("Action", ["Confirm", "Override"], horizontal=True)
    final = st.selectbox("Final decision (if overriding)", VEHICLE_OPTIONS,
                         index=VEHICLE_OPTIONS.index(order.decision)
                         if order.decision in VEHICLE_OPTIONS else 0)
    reason = st.selectbox("Reason (if overriding)", OVERRIDE_REASONS)
    note = st.text_input("Note (optional)")
    if st.form_submit_button("Save and next", type="primary"):
        with db() as con:
            con.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?)", (
                datetime.now(timezone.utc).isoformat(), agent, int(order.order_id),
                order.decision, action.lower(),
                order.decision if action == "Confirm" else final,
                "" if action == "Confirm" else reason, note))
        st.session_state.pos += 0 if hide_done else 1   # handled orders drop out of the list
        st.rerun()

with right.form(f"fix-{order.order_id}"):
    st.markdown("**Correct an item's weight** (saved for next time)")
    item = st.selectbox("Item", lines.name.tolist())
    new_kg = st.number_input("Correct kg for one unit", min_value=0.0, step=0.1)
    if st.form_submit_button("Save correction"):
        old = lines.loc[lines.name == item, "unit_kg"].iloc[0]
        with db() as con:
            con.execute("INSERT INTO item_corrections VALUES (?,?,?,?,?,?)", (
                datetime.now(timezone.utc).isoformat(), agent, int(order.order_id),
                item, float(old) if pd.notna(old) else None, new_kg))
        st.success(f"Saved: {item[:40]} = {new_kg} kg")

st.markdown('<p class="proto">Prototype built for a case study. Not an official Glovo tool.</p>',
            unsafe_allow_html=True)

# ---------- log ----------
with st.expander("Recent actions"):
    with db() as con:
        log = pd.read_sql("SELECT * FROM actions ORDER BY ts DESC LIMIT 20", con)
    st.dataframe(log, hide_index=True, width="stretch")