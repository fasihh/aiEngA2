"""OpenRouter Decisions API adapter (Jev, GPT-6 Luna Decisions, Clef).

The request mirrors the example in the assignment PDF. Responses were checked against
Jev and GPT-6 Luna: answers.next_move.{choice, probabilities, confidence}. Clef is untested.
`parse_response` is the one place to adjust if a model answers in a different shape.
"""
import json

from .base import DECISION_INSTRUCTIONS, move_descriptions
from .http_common import OPENROUTER_BASE, HttpAdapter

QUESTION_KEY = "next_move"


def build_request(model_id, state, recent_moves):
    return {
        "model": model_id,
        "state": {"cube_facelets": state, "recent_moves": recent_moves},
        "questions": {
            QUESTION_KEY: {
                "type": "choice",
                "instructions": DECISION_INSTRUCTIONS,
                "criteria": move_descriptions(),
            }
        },
    }


def parse_response(data):
    """Return (move, confidence) from a Decisions response.

    Tolerant guess at the shape: looks under the question key for a selected
    option (several plausible field names) and an optional probability/confidence,
    which may be a number or a {option: probability} map.
    """
    ans = data
    for key in ("answers", "results", "decisions", QUESTION_KEY):
        if isinstance(ans, dict) and key in ans:
            ans = ans[key]
    if not isinstance(ans, dict):
        return (ans if isinstance(ans, str) else None), None
    move = next((ans[k] for k in ("choice", "selected", "answer", "value", "option") if k in ans), None)
    conf = next((ans[k] for k in ("confidence", "probability", "score") if k in ans), None)
    if isinstance(conf, dict):
        conf = conf.get(move)
    conf = conf if isinstance(conf, (int, float)) else None
    return move, conf


class DecisionsAdapter(HttpAdapter):
    def _attempt(self, state, recent_moves):
        r = self.client.post(
            f"{OPENROUTER_BASE}/alpha/decisions",
            headers=self._headers(),
            json=build_request(self.model_id, state, recent_moves),
        )
        r.raise_for_status()
        data = r.json()
        move, conf = parse_response(data)
        return json.dumps(data), move, conf
