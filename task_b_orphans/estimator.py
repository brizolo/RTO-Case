import json
import os
import time
from dataclasses import dataclass, asdict

CACHE_FILE = "model_cache.json"
BATCH_SIZE = 40          # names per request: few calls, still easy to check
SIZE_CLASSES = ("small", "medium", "large", "bulky")
# Sanity limits: anything outside is treated as a model error, not a fact.
MIN_KG, MAX_KG = 0.005, 60.0


@dataclass
class Estimate:
    category: str       
    unit_kg: float       
    size_class: str     
    keep_upright: bool  
    confidence: str      
    source: str = "model"


DEFAULT_INSTRUCTIONS = """You estimate the physical weight of products sold by a delivery app.
For each item, give the weight of ONE unit as sold (including the food itself,
not the packaging), in kilograms. Items come from many countries and languages.
If you cannot tell what the item is, say so with confidence "low"."""

FORMAT = """
Return a JSON list with one object per item, in the same order, with keys:
  "i": the item's index,
  "category": short English category (e.g. "restaurant dish", "bread", "drink"),
  "unit_kg": number,
  "size_class": one of "small" (fits in a hand), "medium" (shoebox),
                "large" (needs most of a delivery bag), "bulky" (does not fit a bag),
  "keep_upright": true if it can spill (drinks, soups, sauces),
  "confidence": "high", "medium" or "low".

Items (index | country | store | price with currency | name):
"""
PROMPT_FILE = "prompt.txt"


def instructions():
    if os.path.exists(PROMPT_FILE):
        text = open(PROMPT_FILE, encoding="utf-8").read().strip()
        if text:
            return text
    return DEFAULT_INSTRUCTIONS


def build_prompt():
    return instructions() + "\n" + FORMAT


class GeminiEstimator:
    def __init__(self, model=None, api_key=None):
        from google import genai  
        self.client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

    def estimate_batch(self, batch):
        lines = [f'{n} | {b["country"]} | {b["store"]} | {b["price"]} {b["currency"]} | {b["name"]}'
                 for n, b in enumerate(batch)]
        response = self.client.models.generate_content(
            model=self.model,
            contents=build_prompt() + "\n".join(lines),
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        return json.loads(response.text)


def _validate(raw):
    try:
        kg = float(raw["unit_kg"])
        size = str(raw["size_class"]).lower()
        conf = str(raw["confidence"]).lower()
    except (KeyError, TypeError, ValueError):
        return None
    if not (MIN_KG <= kg <= MAX_KG) or size not in SIZE_CLASSES:
        return None
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return Estimate(str(raw.get("category", "unknown")), kg, size,
                    bool(raw.get("keep_upright", False)), conf)


def _load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def estimate_items(items, estimator, pause_s=4.0):
    cache = _load_cache()
    todo = [it for it in {it["name"]: it for it in items}.values()
            if it["name"] not in cache]

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        answers = None
        # Retry with growing waits: "model busy" (503) and quota (429) errors
        # are usually temporary. 15 s, 30 s, 60 s, then give up on this batch.
        for attempt, wait in enumerate([15, 30, 60, None], start=1):
            try:
                answers = estimator.estimate_batch(batch)
                break
            except Exception as e:                  # network, busy, quota, bad JSON
                short = str(e)[:80]
                if wait is None:
                    print(f"Batch {start // BATCH_SIZE} failed {attempt}x ({short}); skipped, rerun later.")
                else:
                    print(f"Batch {start // BATCH_SIZE} attempt {attempt} failed ({short}); waiting {wait}s")
                    time.sleep(wait)
        if answers is None:
            continue
        for raw in answers:
            idx = raw.get("i")
            if isinstance(idx, int) and 0 <= idx < len(batch):
                est = _validate(raw)
                cache[batch[idx]["name"]] = asdict(est) if est else None
        _save_cache(cache)                         # save after every batch
        print(f"Estimated {min(start + BATCH_SIZE, len(todo))}/{len(todo)} new names")
        time.sleep(pause_s)                        # stay inside free-tier limits

    return {it["name"]: (Estimate(**cache[it["name"]]) if cache.get(it["name"]) else None)
            for it in items}