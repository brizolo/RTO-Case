# Glovo RTO case — Brigitta Zolotovitskaia

Two prototypes for the Glovo Real-Time Operations case. The write-ups are the docs; this repo is the code behind them.

| Task | Write-up | Code |
|---|---|---|
| A — closing the knowledge base loop | [Task A doc](https://claude.ai/code/artifact/aae3947f-29d9-4986-8750-37685d731627) | `task_a_kb_loop/` |
| B1 — orphan orders: approach | [B1 doc](https://claude.ai/code/artifact/bdea1132-5d98-4195-ac4a-31aa9690021e) | — |
| B2 — orphan orders: engine and agent screen | [B2 doc](https://claude.ai/code/artifact/03caa965-6dbe-4e97-999c-f519a1a37f09) | `task_b_orphans/` |

One rule runs through both: **the automation acts alone only when every check passes; a person decides everything else.**

```
task_a_kb_loop/    Supervisor answers -> checks -> versioned KB document on the ElevenLabs agent
task_b_orphans/    orphan orders -> weight and size estimate -> vehicle decision -> agent screen
```

## Setup

Python 3.11+. Each folder has its own `requirements.txt`. Run every command **from inside its folder**. API keys are read from environment variables and are never stored in the repo.

```bash
export GEMINI_API_KEY=...
export GEMINI_MODEL=gemini-3.5-flash-lite
```

## Task A — knowledge base loop

```bash
cd task_a_kb_loop
pip install -r requirements.txt

python kbloop.py --today 2026-10-16    # dry run: checks all 14 answers, saves nothing
python kbloop.py                       # real run without pushing: builds versions/MR_vN.md
python kbloop.py --push                # also uploads the document to the ElevenLabs agent
python kbloop.py --rollback 1 --push   # put version 1 back live
```

- Outcomes per answer: `publish`, `hold`, `send_back` (with a suggested fix), `route`, `superseded`, `scheduled`, `duplicate`. Each run writes `run_report.csv`.
- `state.json` remembers which answers were handled, so a rerun never processes one twice. Delete it to start from scratch.
- `versions/` holds every generated document (v1 and v2 went live on the test agent).
- `--push` needs `ELEVENLABS_API_KEY` (Agents: write) and `ELEVENLABS_AGENT_ID_MR`. It updates the agent's **main** branch.
- Rules (banned words, topics, markets) are in `config.json`, not in code.

## Task B — orphan orders

```bash
cd task_b_orphans
pip install -r requirements.txt

python tier1run.py          # weights read from item names (+ declared weight when the name has none)
python tier3run.py          # the rest estimated by the model; cached in model_cache.json
python decide.py            # vehicle, flags, courier notes -> decisions.csv
streamlit run app.py        # the agent screen
```

- `model_cache.json` is included, so `tier3run.py` reruns without an API key or cost.
- Vehicle limits are in `limits.json` (a conservative and an optimistic kg per vehicle), so operations can change them without code.
- Agent actions on the screen are saved locally in `agent_actions.db` (not committed).

## Models used

- Gemini (`gemini-3.5-flash-lite`): weight estimates for item names without a size (Task B) and the contradiction / nothing-new check (Task A).
- ElevenLabs agent: DeepSeek Flash 4.1, chosen over Claude Haiku 4.5 after testing (see A1.5).
- Claude helped me write and debug the code and the docs; the decisions and assumptions are mine and are explained in the docs.
