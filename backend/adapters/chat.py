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


def extract_move(text):
    """Pull the "move" value out of a reply that should be {"move": "..."}.

    Tolerates markdown fences or a sentence around the JSON object, because models without
    structured-output support add them. The move value itself is never repaired: it is
    returned exactly as written and validated strictly by the caller.
    """
    try:
        return json.loads(text).get("move")
    except (ValueError, AttributeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1]).get("move")
        except (ValueError, AttributeError):
            pass
    return None


class ChatAdapter(HttpAdapter):
    def __init__(self, model_id, api_key, label=None, client=None, structured_output=True):
        super().__init__(model_id, api_key, label, client)
        self.structured_output = structured_output   # False for models/routes without json_schema support

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
        body = {"model": self.model_id, "messages": self._messages(state, recent_moves)}
        if self.structured_output:
            body["response_format"] = RESPONSE_FORMAT
        r = self.client.post(f"{OPENROUTER_BASE}/v1/chat/completions", headers=self._headers(), json=body)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"] or ""
        return raw, extract_move(raw), None
