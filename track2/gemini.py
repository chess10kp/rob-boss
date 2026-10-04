"""Thin Gemini wrapper: structured JSON out, retry on overload and per-minute limits."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
T = TypeVar("T", bound=BaseModel)


def make_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY (env var or .env file).")
    return genai.Client(api_key=key)


def generate(client, contents: list, schema: type[T], *, model: str = DEFAULT_MODEL,
             temperature: float = 0.2, attempts: int = 6) -> T:
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=schema, temperature=temperature
    )
    for attempt in range(attempts):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            return schema.model_validate_json(resp.text)
        except errors.ServerError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * 2 ** attempt)
        except errors.ClientError as e:
            # Per-minute limit: wait and retry. A daily cap is not recoverable here.
            if e.code != 429 or attempt == attempts - 1 or "PerDay" in str(e):
                raise
            time.sleep(15)
    raise RuntimeError("unreachable")
