"""OpenRouter chat-completions adapter (used for Claude Opus 5.5 and free stand-ins)."""
import json

from ..cube import MOVES
from .base import INSTRUCTIONS, move_descriptions
from .http_common import OPENROUTER_BASE, HttpAdapter

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "cube_move",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"move": {"type": "string", "enum": MOVES}},
            "required": ["move"],
            "additionalProperties": False,
        },
    },
}


class ChatAdapter(HttpAdapter):
    def _messages(self, state, recent_moves):
        options = "\n".join(f"- {m}: {d}" for m, d in move_descriptions().items())
        user = (
            f"Current cube state: {state}\n"
            f"Recent moves (oldest first): {json.dumps(recent_moves)}\n"
            f"Allowed moves:\n{options}\n\n"
            'Reply with exactly one move as JSON: {"move": "<move>"}'
        )
        return [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": user}]

    def _attempt(self, state, recent_moves):
        r = self.client.post(
            f"{OPENROUTER_BASE}/v1/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model_id,
                "messages": self._messages(state, recent_moves),
                "response_format": RESPONSE_FORMAT,
            },
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
        return raw, json.loads(raw).get("move"), None
