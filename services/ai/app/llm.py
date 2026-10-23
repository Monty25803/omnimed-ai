"""Optional OpenAI-compatible LLM drafting. Every draft goes through the grounding engine."""
from __future__ import annotations

import json
import os

import httpx

API_KEY = os.getenv("LLM_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

SCRIBE_PROMPT = """You are a clinical documentation assistant. From the consultation transcript, produce JSON only:
{"assessment":[{"display":"<diagnosis as a standard clinical term>"}],
 "medications":[{"drug":"<generic ingredient name>","strength_mg":<number>,"frequency":"OD|BD|TDS|QID|HS|PRN|STAT","duration_days":<int or null>,"action":"start|stop|continue"}],
 "tests":[{"display":"<lab or investigation name>"}],
 "narrative":{"S":"...","O":"...","A":"...","P":"..."}}
Only include items explicitly stated in the transcript. Do not invent diagnoses, drugs, doses or tests."""


def enabled() -> bool:
    return bool(API_KEY)


async def draft(system_prompt: str, user: str) -> dict:
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(f"{BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {API_KEY}"},
                                 json={"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                                       "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]})
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])
