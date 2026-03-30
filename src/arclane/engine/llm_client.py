"""LLM client for Arclane — routes through Nexus gateway when available,
falls back to direct OpenAI-compatible endpoint.

Nexus provides: multi-provider ensemble, cost tracking, model selection,
and fallback across 6+ providers. Direct mode is kept as a safety net.
"""

import asyncio
import json
import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("llm_client")


class ArclaneLLMClient:
    """LLM client that prefers Nexus gateway, falls back to direct provider.

    Supports both OpenAI-compatible and Anthropic API formats.
    Set ``LLM_BASE_URL=https://api.anthropic.com`` to use Anthropic models.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int | None = None,
        nexus_url: str | None = None,
    ):
        # Direct provider (fallback)
        self._base_url = (base_url or settings.llm_base_url).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.llm_api_key
        self._model = model or settings.llm_model
        self._timeout_s = timeout_s or settings.llm_timeout_s

        # Nexus gateway (preferred)
        self._nexus_url = (nexus_url or settings.nexus_base_url).rstrip("/")
        self._nexus_failures = 0
        self._nexus_max_failures = 3  # Circuit breaker: skip Nexus after 3 consecutive failures

        # Per-area model routing
        self._model_map: dict[str, str] = {}
        if settings.llm_model_map:
            try:
                self._model_map = json.loads(settings.llm_model_map)
            except json.JSONDecodeError:
                log.warning("Invalid LLM_MODEL_MAP JSON — ignoring")

    def model_for_area(self, area: str) -> str:
        """Return the model ID to use for a given task area."""
        return self._model_map.get(area, self._model)

    @property
    def _is_anthropic(self) -> bool:
        return "anthropic.com" in self._base_url

    @property
    def enabled(self) -> bool:
        return bool((self._base_url and self._model) or self._nexus_url)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> str | None:
        """Return model output or None when the request cannot be completed.

        Args:
            model: Optional model override (e.g. from per-area routing).
                   Falls back to the default ``llm_model`` when not provided.

        Tries Nexus gateway first (ensemble routing, cost tracking).
        Falls back to direct provider if Nexus is unavailable.
        """
        if not self.enabled:
            return None

        effective_model = model or self._model

        # Try Nexus gateway first (unless circuit breaker tripped or direct provider is configured)
        # Skip Nexus when a direct LLM endpoint is explicitly configured (e.g. sandbox proxy)
        if self._nexus_url and self._nexus_failures < self._nexus_max_failures and not self._base_url:
            result = await self._generate_via_nexus(system_prompt, user_prompt, temperature, max_tokens)
            if result is not None:
                self._nexus_failures = 0  # Reset on success
                return result
            self._nexus_failures += 1
            if self._nexus_failures >= self._nexus_max_failures:
                log.warning("Nexus gateway circuit breaker tripped — using direct provider")

        # Fall back to direct provider
        return await self._generate_direct(system_prompt, user_prompt, temperature, max_tokens, effective_model)

    async def _generate_via_nexus(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
    ) -> str | None:
        """Route LLM call through Nexus shared gateway."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    f"{self._nexus_url}/api/generate",
                    json={
                        "prompt": user_prompt,
                        "system_prompt": system_prompt,
                        "max_tokens": max_tokens,
                        "source": "arclane",
                    },
                )
                response.raise_for_status()

            data = response.json()
            content = data.get("content", "")
            if content:
                log.debug(
                    "Nexus LLM: model=%s latency=%.0fms",
                    data.get("model", "?"), data.get("latency_ms", 0),
                )
                return content.strip() or None
        except Exception:
            log.debug("Nexus gateway unavailable, will try direct", exc_info=True)

        return None

    async def _generate_direct(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
        model: str | None = None,
    ) -> str | None:
        """Direct provider call — auto-detects Anthropic vs OpenAI-compatible format."""
        effective_model = model or self._model
        if not self._base_url or not effective_model:
            return None

        if self._is_anthropic:
            return await self._generate_anthropic(system_prompt, user_prompt, temperature, max_tokens, effective_model)
        return await self._generate_openai(system_prompt, user_prompt, temperature, max_tokens, effective_model)

    async def _generate_anthropic(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
        model: str | None = None,
    ) -> str | None:
        """Call the Anthropic Messages API with retry on 429/529."""
        effective_model = model or self._model
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": effective_model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(
                        f"{self._base_url}/v1/messages",
                        json=payload,
                        headers=headers,
                    )
                    if response.status_code in (429, 529):
                        retry_after = float(response.headers.get("retry-after", 2 ** attempt))
                        log.warning(
                            "Anthropic %d (attempt %d/3, model=%s) — retrying in %.1fs",
                            response.status_code, attempt + 1, effective_model, retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    response.raise_for_status()
            except Exception as exc:
                last_error = exc
                log.warning("Anthropic LLM request failed (attempt %d/3)", attempt + 1, exc_info=True)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                continue

            try:
                data = response.json()
                content = "".join(
                    block["text"] for block in data["content"] if block["type"] == "text"
                )
            except (KeyError, IndexError, TypeError):
                log.warning("Anthropic response did not include message content")
                return None

            content = content.strip()
            return content or None

        log.warning("Anthropic LLM exhausted retries (model=%s): %s", effective_model, last_error)
        return None

    async def _generate_openai(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
        model: str | None = None,
    ) -> str | None:
        """Call an OpenAI-compatible /v1/chat/completions endpoint."""
        effective_model = model or self._model
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except Exception:
            log.warning("LLM request failed; falling back to deterministic output", exc_info=True)
            return None

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("LLM response did not include message content")
            return None

        content = content.strip()
        return content or None
