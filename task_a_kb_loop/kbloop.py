import argparse
import csv
import json
import os
import re
from datetime import date, datetime

CFG = json.load(open("config.json"))
STATE_FILE = "state.json"
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {"handled": {}, "live": {}, "versions": {}, "doc_ids": {}}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w"), indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- checks
def topic_of(question):
    for topic, pattern in CFG["topics"].items():
        if re.search(pattern, question, re.IGNORECASE):
            return topic
    return "other"


def banned_words(text):
    found = [w for w in CFG["banned"] if re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE)]
    # whole phrases first, so "our riders" is not also reported as "riders"
    return sorted(set(found), key=len, reverse=True)


def personal_data(text):
    issues = []
    if re.search(r"\b\d[\d ]{6,}\d\b", text):
        issues.append("a phone, wallet or ID number")
    if re.search(r"\btell (him|her|them)\b", text, re.IGNORECASE):
        issues.append("written for one caller ('tell him')")
    return issues


def is_non_answer(text):
    t = text.strip().lower()
    return bool(re.match(r"^(check|ask|contact|see|refer)\b", t)) or "check with" in t


def effective_date(text):
    m = re.search(r"from (\d{1,2}) ([a-z]+) (\d{4})", text, re.IGNORECASE)
    if m and m.group(2).lower() in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def announces_change(text):
    return bool(re.search(r"\bfrom \d{1,2} [a-z]+|\(was\b|\bnow\b|\bno longer\b", text, re.IGNORECASE))


def suggested_fix(text):
    fixed = text
    for word, replacement in CFG["banned"].items():
        def keep_case(m, rep=replacement):
            return rep[0].upper() + rep[1:] if m.group(0)[0].isupper() else rep
        fixed = re.sub(rf"\b{re.escape(word)}\b", keep_case, fixed, flags=re.IGNORECASE)
    # drop sentences with personal data or addressed to one caller
    sentences = re.split(r"(?<=[.!?])\s+", fixed)
    keep = [s for s in sentences if not personal_data(s)]
    fixed = " ".join(keep).strip()
    fixed = re.sub(r"^In general:\s*", "", fixed)
    return fixed[0].upper() + fixed[1:] if fixed else fixed


class GeminiChecker:
    PROMPT = (
        "You check answers before they are added to a voice agent's knowledge base.\n"
        "KNOWLEDGE BASE:\n{kb}\n\nANSWERS ALREADY APPROVED ON THIS TOPIC:\n{live}\n\n"
        "NEW ANSWER (question: {q}):\n{a}\n\n"
        "1) Does the new answer contradict anything the knowledge base states, or allow "
        "something it forbids? 2) Does it add information that is not already in the "
        "knowledge base or the approved answers? Reply as JSON: "
        '{{"contradicts": true|false, "kb_quote": "the sentence it contradicts or empty", '
        '"adds_new_information": true|false, "explanation": "one sentence"}}'
    )

    def __init__(self):
        from google import genai 
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

    def check(self, kb, question, answer, live=""):
        import time
        waits = [10, 20, 40]        
        for attempt in range(len(waits) + 1):
            try:
                r = self.client.models.generate_content(
                    model=self.model,
                    contents=self.PROMPT.format(kb=kb, q=question, a=answer, live=live or "(none)"),
                    config={"response_mime_type": "application/json", "temperature": 0},
                )
                res = json.loads(r.text)
                if isinstance(res, list):   
                    res = res[0] if res and isinstance(res[0], dict) else {}
                if "contradicts" not in res:
                    raise ValueError("model reply missing 'contradicts'")
                return res
            except Exception as e:
                busy = any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
                if not busy or attempt == len(waits):
                    raise   
                print(f"  model busy, retrying in {waits[attempt]}s ...")
                time.sleep(waits[attempt])


def contradiction(checker, kb, q, a, live=""):
    if checker is None:
        return "unchecked", "contradiction check not available (no GEMINI_API_KEY)"
    try:
        res = checker.check(kb, q, a, live)
        if res.get("contradicts"):
            return "contradicts", f"{res.get('explanation', '')} KB says: \"{res.get('kb_quote', '')}\""
        if res.get("adds_new_information") is False:
            return "duplicate", res.get("explanation", "")
        return "ok", ""
    except Exception as e:  
        return "unchecked", f"contradiction check failed ({str(e)[:60]})"


def decide_all(rows, state, checker, today):
    kb_text = {m: open(c["kb_text"], encoding="utf-8").read() for m, c in CFG["agents"].items()}
    new = [r for r in rows if r["question_id"] not in state["handled"]]
    for r in new:
        r["topic"] = topic_of(r["caller_question"])
        r["logged"] = datetime.strptime(r["logged_at"], "%Y-%m-%d %H:%M")

    results = []
    for r in new:
        a, market = r["supervisor_answer"], r["market"]
        out = {"question_id": r["question_id"], "market": market, "topic": r["topic"],
               "outcome": "", "reason": "", "suggested_fix": ""}

        if market not in CFG["agents"]:
            out.update(outcome="route", reason=f"market {market} has no agent here; route to the {market} team")
            results.append(out); continue
        
        problems = []
        if is_non_answer(a):
            problems.append("not an answer (refers the caller elsewhere)")
        bw = banned_words(a)
        if bw:
            problems.append("banned words: " + ", ".join(bw))
        pd_ = personal_data(a)
        if pd_:
            problems.append("personal data: " + ", ".join(pd_))
        if problems:
            fix = "" if is_non_answer(a) else suggested_fix(a)
            out.update(outcome="send_back", reason="; ".join(problems), suggested_fix=fix)
            results.append(out); continue

        newer = [o for o in new if o is not r and o["market"] == market and o["topic"] == r["topic"]
                 and o["topic"] != "other" and o["logged"] > r["logged"]
                 and announces_change(o["supervisor_answer"])]
        if newer:
            out.update(outcome="superseded", reason=f"replaced by {newer[-1]['question_id']}")
            results.append(out); continue

        same_topic = [x for x in list(state["live"].get(market, {}).values()) +
                      [p for p in results if p["outcome"] == "publish" and p["market"] == market]
                      if x.get("topic") == r["topic"] and r["topic"] != "other"]
        live_text = "\n".join(f"- {x['answer']}" for x in same_topic)
        status, detail = contradiction(checker, kb_text[market], r["caller_question"], a, live_text)
        if status == "duplicate":
            out.update(outcome="duplicate", reason="adds nothing to approved answers: " + detail)
            results.append(out); continue
        if status != "ok":
            out.update(outcome="hold", reason=("contradicts the KB: " if status == "contradicts" else "") + detail,
                       retry=(status == "unchecked"))  
            results.append(out); continue

        # 5. Correct but not valid yet?
        eff = effective_date(a)
        if eff and eff > today:
            out.update(outcome="scheduled", reason=f"valid from {eff.isoformat()}")
            results.append(out); continue

        out.update(outcome="publish", reason="passed every check")
        out["answer"], out["question"] = a, r["caller_question"]
        results.append(out)
    return results

def build_document(market, live):
    lines = [f"Supervisor answers - Meridia ({market})", "",
             "Answers approved after the original knowledge base was written. "
             "Where they differ, these answers are the more recent ones.", ""]
    by_topic = {}
    for item in live.values():
        by_topic.setdefault(item["topic"], []).append(item)
    for topic, items in sorted(by_topic.items()):
        for it in items:
            lines += [f"Q: {it['question']}", f"A: {it['answer']}", ""]
    return "\n".join(lines)


def push_to_elevenlabs(market, text, version, state):
    import requests
    key = os.environ["ELEVENLABS_API_KEY"]
    agent_id = os.environ[CFG["agents"][market]["agent_env"]]
    h = {"xi-api-key": key, "Content-Type": "application/json"}
    base = "https://api.elevenlabs.io/v1/convai"
    name = f"Supervisor answers - {market} - v{version}"

    doc = requests.post(f"{base}/knowledge-base/text", headers=h, json={"text": text, "name": name}, timeout=60)
    if not doc.ok:   
        raise SystemExit(f"ElevenLabs upload failed ({doc.status_code}): {doc.text[:300]}\n"
                         "Nothing was changed on the agent and nothing was saved; fix and rerun.")
    new_id = doc.json()["id"]

    agent = requests.get(f"{base}/agents/{agent_id}", headers=h, timeout=60).json()
    kb = agent["conversation_config"]["agent"]["prompt"].get("knowledge_base", [])
    kb = [d for d in kb if not d.get("name", "").startswith(f"Supervisor answers - {market}")]
    kb.append({"type": "text", "name": name, "id": new_id, "usage_mode": "auto"})
    patch = {"conversation_config": {"agent": {"prompt": {"knowledge_base": kb}}}}
    requests.patch(f"{base}/agents/{agent_id}", headers=h, json=patch, timeout=60).raise_for_status()

    old_id = state["doc_ids"].get(market)
    if old_id:
        requests.delete(f"{base}/knowledge-base/{old_id}", headers=h, timeout=60)
    state["doc_ids"][market] = new_id
    return new_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="supervisor_answers.csv")
    ap.add_argument("--push", action="store_true", help="really update ElevenLabs")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD (for testing)")
    ap.add_argument("--rollback", type=int, help="put this version of the MR document back live")
    args = ap.parse_args()

    state = load_state()
    os.makedirs("versions", exist_ok=True)

    if args.rollback:
        text = open(f"versions/MR_v{args.rollback}.md", encoding="utf-8").read()
        print(f"Rolling back MR to v{args.rollback}")
        if args.push:
            push_to_elevenlabs("MR", text, f"{args.rollback}-restored", state)
            save_state(state)
        return

    today = date.fromisoformat(args.today) if args.today else date.today()
    rows = list(csv.DictReader(open(args.input, encoding="utf-8")))
    try:
        checker = GeminiChecker() if os.environ.get("GEMINI_API_KEY") else None
    except Exception:
        checker = None

    results = decide_all(rows, state, checker, today)

    for r in results:
        if r.get("retry"):
            continue       
        state["handled"][r["question_id"]] = r["outcome"]
        if r["outcome"] == "publish":
            state["live"].setdefault(r["market"], {})[r["question_id"]] = {
                "topic": r["topic"], "question": r["question"], "answer": r["answer"]}
        # a superseded answer must also leave the live document if it was there
        if r["outcome"] == "superseded":
            state["live"].get(r["market"], {}).pop(r["question_id"], None)

    for market in CFG["agents"]:
        if not any(r["outcome"] == "publish" and r["market"] == market for r in results):
            continue
        version = state["versions"].get(market, 0) + 1
        text = build_document(market, state["live"].get(market, {}))
        with open(f"versions/{market}_v{version}.md", "w", encoding="utf-8") as f:
            f.write(text)
        state["versions"][market] = version
        if args.push:
            new_id = push_to_elevenlabs(market, text, version, state)
            print(f"{market}: v{version} live on the agent (document {new_id})")
        else:
            print(f"{market}: v{version} built in versions/ (dry run, not pushed)")

    with open("run_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["question_id", "market", "topic", "outcome", "reason", "suggested_fix"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    if args.push or not args.today:   
        save_state(state)

    print(f"\n{len(results)} new answers:")
    for r in results:
        print(f"  {r['question_id']}  {r['outcome']:<10} {r['reason'][:90]}")


if __name__ == "__main__":
    main()