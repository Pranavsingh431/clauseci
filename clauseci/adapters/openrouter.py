"""
OpenRouter adapter for the one semantic component.

One pinned model. One request, and at most one repair attempt if the reply is
not valid against the schema. No recursive self correction.

The model is given text and returns JSON. It is given no tools and no network,
so it cannot act on anything it reads.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from clauseci.versions import (
    SEMANTIC_MAX_REPAIR_ATTEMPTS,
    SEMANTIC_MODEL,
    SEMANTIC_TEMPERATURE,
    SEMANTIC_TIMEOUT_SECONDS,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class SemanticError(RuntimeError):
    """The model could not produce valid structured output."""


@dataclass
class Usage:
    """Accumulated cost and latency. Never contains a credential."""

    requests: int = 0
    repairs: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_reported: bool = False
    latencies: list[float] = field(default_factory=list)

    def add(self, response: Any, seconds: float, repair: bool) -> None:
        self.requests += 1
        if repair:
            self.repairs += 1
        self.latencies.append(seconds)
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
            cost = getattr(usage, "cost", None)
            if cost is not None:
                self.cost_usd += float(cost)
                self.cost_reported = True

    @property
    def latency_total(self) -> float:
        return sum(self.latencies)

    @property
    def latency_max(self) -> float:
        return max(self.latencies) if self.latencies else 0.0


class SemanticClient:
    """Structured JSON completions from one pinned model."""

    def __init__(self, api_key: str | None = None, model: str = SEMANTIC_MODEL) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise SemanticError("OPENROUTER_API_KEY is missing from the environment")
        self.model = model
        self._client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key,
                              timeout=SEMANTIC_TIMEOUT_SECONDS)

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        usage: Usage,
        max_tokens: int = 3000,
    ) -> dict[str, Any]:
        """
        One structured request, plus at most one repair attempt.

        The repair attempt resends the same task with the parse error appended.
        It does not change the schema or relax any constraint.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_error: str | None = None
        for attempt in range(SEMANTIC_MAX_REPAIR_ATTEMPTS + 1):
            started = time.monotonic()
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=SEMANTIC_TEMPERATURE,
                max_tokens=max_tokens,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
                extra_body={"usage": {"include": True}},
            )
            usage.add(response, time.monotonic() - started, repair=attempt > 0)

            text = response.choices[0].message.content or ""
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                last_error = f"reply was not valid JSON: {exc}"
            else:
                if isinstance(parsed, dict):
                    return parsed
                last_error = "reply was valid JSON but not an object"

            if attempt < SEMANTIC_MAX_REPAIR_ATTEMPTS:
                messages = messages + [
                    {"role": "assistant", "content": text[:2000]},
                    {
                        "role": "user",
                        "content": (
                            f"That reply could not be used: {last_error}. "
                            f"Send the same answer again as a single JSON object "
                            f"matching the schema, with nothing else around it."
                        ),
                    },
                ]

        raise SemanticError(
            f"model did not return usable JSON after "
            f"{SEMANTIC_MAX_REPAIR_ATTEMPTS + 1} attempts: {last_error}"
        )
