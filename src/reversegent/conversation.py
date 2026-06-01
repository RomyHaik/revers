"""TargetConversation — a multi-turn dialogue with the target agent.

Centralizes how conversation context reaches the target across target types:

- OpenAI / HTTP targets are STATELESS — the only way to give them prior context
  is to replay the full message history on every call. So we send
  `history + new_messages` each turn.
- Browser targets are STATEFUL — the live chat thread already holds the history
  server-side (for presets with reset_between_probes=False). Replaying old turns
  would re-type them, so we send only the new turn(s) and let the page remember.

`new_messages` may contain arbitrary roles (user / assistant / system), which on
stateless targets enables context-injection attacks — planting a fabricated
assistant or system turn the model never actually produced. On browser targets
those fabricated turns can't be injected into someone else's UI, so they are
flattened into the typed user text as a best-effort fallback.
"""

from __future__ import annotations

import logging

from reversegent.clients import ClientFactory
from reversegent.config import ReversegentConfig

log = logging.getLogger(__name__)


class TargetConversation:
    def __init__(self, clients: ClientFactory, config: ReversegentConfig) -> None:
        self.clients = clients
        self.config = config
        self.history: list[dict] = []

    @property
    def _stateless(self) -> bool:
        return self.config.target_type in ("openai", "http")

    def reset(self) -> None:
        self.history = []

    def send(self, new_messages: list[dict]) -> str:
        """Send one turn (one or more messages) and return the target's reply."""
        if not new_messages:
            return ""
        if self._stateless:
            payload = self.history + new_messages
        else:
            payload = self._flatten_for_browser(new_messages)
        reply = self.clients.query_target(payload)
        text = (reply.content or "").strip()
        self.history.extend(new_messages)
        self.history.append({"role": "assistant", "content": text})
        return text

    def send_user(self, message: str) -> str:
        return self.send([{"role": "user", "content": message}])

    def _flatten_for_browser(self, new_messages: list[dict]) -> list[dict]:
        """Browser UIs can only accept typed user text. Fabricated assistant/
        system turns are folded into the user text so the injection intent
        survives as content (clearly the strongest a UI allows)."""
        parts: list[str] = []
        for m in new_messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                parts.append(content)
            else:
                parts.append(f"[{role}]: {content}")
        return [{"role": "user", "content": "\n\n".join(parts)}]

    def transcript(self, limit: int = 0) -> str:
        msgs = self.history[-limit:] if limit else self.history
        return "\n".join(f"{m.get('role', '?').upper()}: {m.get('content', '')}" for m in msgs)

    def turn_count(self) -> int:
        return sum(1 for m in self.history if m.get("role") == "user")
