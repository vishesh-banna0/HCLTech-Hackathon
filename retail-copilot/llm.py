"""The sole provider boundary for both agents."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))


def complete(messages):
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "").strip()
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
    if not key or not model:
        raise RuntimeError("Configure OPENROUTER_API_KEY and exact OPENROUTER_MODEL in .env first.")
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base, timeout=25, max_retries=1)
    return client.chat.completions.create(
        model=model, messages=messages, temperature=0
    ).choices[0].message.content or ""
