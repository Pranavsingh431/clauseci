"""Probe candidate OpenRouter models for strict structured-output support + reasoning."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_util as E
E.load()
from openai import OpenAI

cl = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

SCHEMA = {"type": "object", "properties": {
    "controlling_document": {"type": "string"},
    "max_retention_days": {"type": "integer"},
    "violation": {"type": "boolean"},
    "reason": {"type": "string"}},
    "required": ["controlling_document", "max_retention_days", "violation", "reason"],
    "additionalProperties": False}

# Same trap as the real demo: old 180d, signed 30d, newer-but-UNSIGNED 365d.
PROMPT = """Three documents exist for customer acme-corp:

[A] DPA, 14 Jan 2025, STATUS: EXECUTED — "logs may be retained up to one hundred and eighty (180) days"
[B] Amendment No.1, 20 Aug 2026, STATUS: EXECUTED, FULLY SIGNED — "Notwithstanding any prior agreement,
    Customer Data including application logs shall not be retained for more than thirty (30) days"
[C] Draft Amendment No.2, proposed 1 Oct 2026, STATUS: DRAFT - NOT EXECUTED, NOT BINDING —
    "proposing to extend retention to three hundred and sixty-five (365) days"

A pull request sets acme-corp application_logs_days to 90.
Which document controls, what is the binding cap, and is this a violation?"""

CANDIDATES = [
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-4.1-mini",
    "google/gemini-2.5-flash",
    "deepseek/deepseek-chat-v3.1",
]

print(f"{'model':<34} {'ok':<5} {'doc':<6} {'days':<6} {'viol':<6} {'tok':<7} {'s':<6} cost")
print("-" * 86)
for m in CANDIDATES:
    t0 = time.time()
    try:
        r = cl.chat.completions.create(
            model=m, temperature=0, max_tokens=500,
            messages=[{"role": "system", "content": "Reply only with JSON matching the schema."},
                      {"role": "user", "content": PROMPT}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA}},
            extra_body={"usage": {"include": True}})
        d = json.loads(r.choices[0].message.content)
        dt = time.time() - t0
        doc = "B" if "amendment no.1" in d["controlling_document"].lower() or d["controlling_document"].strip().upper().startswith("B") else d["controlling_document"][:5]
        correct = d["max_retention_days"] == 30 and d["violation"] is True
        cost = getattr(r.usage, "cost", None)
        print(f"{m:<34} {'PASS' if correct else 'WRONG':<5} {doc:<6} {d['max_retention_days']:<6} "
              f"{str(d['violation']):<6} {r.usage.total_tokens:<7} {dt:<6.1f} "
              f"{('$%.5f' % cost) if cost else '?'}")
    except Exception as e:
        print(f"{m:<34} ERROR  {type(e).__name__}: {str(e)[:44]}")
