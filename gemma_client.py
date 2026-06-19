"""
gemma_client.py — Ollama-backed LLM client for structured inference.

Wraps Ollama's ``/api/generate`` and ``/api/chat`` endpoints.
Designed for TAK-ML integration: tight JSON schemas, zero free-text.

Usage:
    from gemma_client import GemmaClient
    client = GemmaClient()
    result = client.generate_json("gemma3:1b", prompt_text)  # → parsed dict/list

    # Or using chat API with system prompt:
    result = client.chat_json("gemma3:1b", system_prompt, user_message)

Ollama API reference (from ollama-main/api/types.go):
    POST /api/generate → { model, prompt, stream, format, options }
    POST /api/chat     → { model, messages[], stream, format, options }
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class GemmaClient:
    """
    Stateless client for Ollama's REST API.

    Supports both ``/api/generate`` (single-shot) and ``/api/chat`` (multi-turn).
    Forces ``stream: false`` and ``format: "json"`` for deterministic structured output.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 45.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Cache for model availability: {model_name: (timestamp, details)}
        self._model_ready_cache: Dict[str, tuple[float, Dict]] = {}

    def _probe_model(self, model: str) -> bool:
        """
        Check if model is loaded/loadable via /api/show.
        Caches result for 30s to avoid hammering.
        """
        import requests
        now = time.time()

        # Check cache
        if model in self._model_ready_cache:
            ts, _ = self._model_ready_cache[model]
            if now - ts < 30:  # 30s cache validity
                return True

        try:
            # Capability probe
            resp = requests.post(
                f"{self.base_url}/api/show",
                json={"name": model},
                timeout=5
            )
            if resp.status_code == 200:
                self._model_ready_cache[model] = (now, resp.json())
                return True
            elif resp.status_code == 404:
                # Model not found or strict 404
                return False
            else:
                return False
        except Exception as e:
            logger.warning(f"Model probe failed for {model}: {e}")
            return False

    def _wait_for_model(self, model: str, retries: int = 5) -> bool:
        """
        Backoff + warmup guard.  5 attempts with 2s base exponential backoff.
        """
        for i in range(retries):
            if self._probe_model(model):
                return True
            if i < retries - 1:
                sleep_time = 2.0 * (1.5 ** i)  # 2.0, 3.0, 4.5, 6.75
                logger.info(f"Waiting for model {model} (attempt {i+1}/{retries}, sleeping {sleep_time:.1f}s)...")
                time.sleep(sleep_time)
        return False

    # ─────────────────────────────────────────────────────────────────────
    # /api/generate
    # ─────────────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        format_json: bool = True,
    ) -> Dict[str, Any]:
        """
        Call ``POST /api/generate`` (synchronous, non-streaming).
        Includes warmup checks and retries.
        """
        import requests

        # 1. Probe & Warmup
        if not self._wait_for_model(model):
            logger.error(f"Model {model} unavailable after retries.")
            return {
                "status": "degraded",
                "reason": "LLM warming up or unavailable",
                "fallback": "rule-based GraphOps only",
                "response": '{"error": "LLM unavailable"}' # Minimal valid JSON-like string
            }

        body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            body["system"] = system
        if format_json:
            body["format"] = "json"

        # 2. Execute Request — with retry + exponential backoff
        max_retries = 2
        last_error = None
        for attempt in range(max_retries):
            try:
                t0 = time.monotonic()
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                elapsed = time.monotonic() - t0

                logger.debug(
                    "[gemma] generate %s → %d chars in %.2fs (eval_count=%s)",
                    model,
                    len(data.get("response", "")),
                    elapsed,
                    data.get("eval_count", "?"),
                )
                return data
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    backoff = 3.0 * (2 ** attempt)  # 3s, 6s
                    logger.warning(
                        "[gemma] generate timeout (attempt %d/%d), "
                        "retrying in %.1fs: %s",
                        attempt + 1, max_retries, backoff, e,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "[gemma] generate failed after %d attempts: %s",
                        max_retries, e,
                    )
            except Exception as e:
                logger.error(f"Generate failed: {e}")
                return {
                    "status": "error",
                    "reason": str(e),
                    "response": "{}"
                }

        return {
            "status": "error",
            "reason": f"Timeout after {max_retries} attempts: {last_error}",
            "response": "{}",
        }

    def generate_json(
        self,
        model: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
    ) -> Union[Dict, List]:
        """
        Call ``/api/generate`` and parse the response as JSON.

        Returns parsed JSON (dict or list).
        Raises ``ValueError`` if the model output is not valid JSON or if an error occurred.
        """
        data = self.generate(
            model, prompt, system=system, temperature=temperature, format_json=True,
        )

        # Handle fallback/degraded/error state
        if data.get("status") in ("degraded", "error"):
             reason = data.get("reason", "Unknown LLM error")
             logger.warning("LLM request failed: %s", reason)
             raise ValueError(f"LLM request failed: {reason}")

        txt = data.get("response", "").strip()
        return self._parse_json(txt)

    # ─────────────────────────────────────────────────────────────────────
    # /api/chat (Refactored to use generate)
    # ─────────────────────────────────────────────────────────────────────

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.0,
        format_json: bool = True,
    ) -> Dict[str, Any]:
        """
        Adapts chat messages to a prompted generation call.
        Prefer ``/api/generate`` for stability.
        """
        # Construct prompt from messages
        prompt_parts = []
        system_msg = None

        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            if role == "SYSTEM":
                system_msg = content # Extract system for API field if needed, or embed
                prompt_parts.append(f"<SYSTEM>\n{content}\n</SYSTEM>")
            else:
                prompt_parts.append(f"<{role}>\n{content}\n</{role}>")

        full_prompt = "\n".join(prompt_parts)

        # Use generate instead of chat endpoint
        return self.generate(
            model=model,
            prompt=full_prompt,
            # We don't verify if 'system' param helps with Gemma when embedding in prompt,
            # but passing it explicitly is safer if extracting.
            # However, since we embedded it, let's just pass the full prompt.
            system=None,
            temperature=temperature,
            format_json=format_json
        )

    def chat_json(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        *,
        temperature: float = 0.0,
    ) -> Union[Dict, List]:
        """
        Convenience: system + user message → parsed JSON response.
        """
        # We can construct the prompt manually to ensure it uses the generate path
        prompt = f"<SYSTEM>\n{system_prompt}\n</SYSTEM>\n<USER>\n{user_message}\n</USER>"

        data = self.generate(
            model,
            prompt,
            system=None,
            temperature=temperature,
            format_json=True
        )

        if data.get("status") == "degraded":
            return {"status": "degraded", "info": "LLM unavailable"}

        txt = data.get("response", "").strip()
        try:
            return self._parse_json(txt)
        except ValueError:
            return {}


    # ─────────────────────────────────────────────────────────────────────
    # Health / model listing
    # ─────────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if the Ollama server is reachable."""
        try:
            import requests
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.ok
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """List locally available model names."""
        try:
            import requests
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [m.get("name", "") for m in models]
        except Exception:
            return []

    # ─────────────────────────────────────────────────────────────────────
    # JSON parsing helpers
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(txt: str) -> Union[Dict, List]:
        """
        Parse JSON from model output, tolerating markdown fences and trailing text.
        """
        # Strip markdown code fences if present
        txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.MULTILINE)
        txt = re.sub(r"```\s*$", "", txt, flags=re.MULTILINE)
        txt = txt.strip()

        if not txt:
            raise ValueError("Empty model response")

        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            pass

        # Try to extract first JSON object or array
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = txt.find(start_char)
            if start >= 0:
                depth = 0
                in_str = False
                esc = False
                for i in range(start, len(txt)):
                    c = txt[i]
                    if esc:
                        esc = False
                        continue
                    if c == "\\":
                        esc = True
                        continue
                    if c == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if c == start_char:
                        depth += 1
                    elif c == end_char:
                        depth -= 1
                        if depth == 0:
                            candidate = txt[start:i + 1]
                            try:
                                return json.loads(candidate)
                            except json.JSONDecodeError:
                                break

        raise ValueError(f"Could not parse JSON from model output: {txt[:200]}")
