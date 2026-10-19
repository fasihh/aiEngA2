"""Shared contract for every model adapter.

All adapters expose `get_move(state, recent_moves) -> MoveResult`. The wording
shown to the models lives here so the three Decisions models and the chat model
receive identical instructions and the same 18 move definitions.
"""
from dataclasses import dataclass, field

from ..cube import FACES, MOVES

# Policy applied identically to every model.
TIMEOUT_S = 60.0
MAX_RETRIES = 1          # one retry after a timeout/error/malformed reply
RECENT_MOVES_N = 5       # how many of the latest applied moves the model sees

FACE_NAMES = {
    "U": "upper", "D": "down", "L": "left", "R": "right", "F": "front", "B": "back",
}

INSTRUCTIONS = (
    "You are solving a 3x3 Rubik's Cube. The cube is given as a 54-character facelet "
    "string in the order U(0-8) R(9-17) F(18-26) D(27-35) L(36-44) B(45-53), each face "
    "read row by row as seen from outside; letters are the face each sticker belongs to, "
    "so the solved cube is 'U'*9+'R'*9+'F'*9+'D'*9+'L'*9+'B'*9. "
    "Choose the single next move most likely to progress toward the solved cube."
)


def move_descriptions():
    """{move: plain-English description} for all 18 moves."""
    out = {}
    for f in FACES:
        name = FACE_NAMES[f]
        out[f] = f"Turn the {name} face clockwise by 90 degrees"
        out[f + "'"] = f"Turn the {name} face counterclockwise by 90 degrees"
        out[f + "2"] = f"Turn the {name} face by 180 degrees"
    return out


@dataclass
class MoveResult:
    """Outcome of one decision step (including any retries)."""
    move: str | None            # None if no valid move was obtained
    latency_s: float            # total wall time of the step, retries included
    raw: str = ""               # last raw response text, for logging
    valid: bool = True
    confidence: float | None = None
    retries: int = 0
    errors: list[str] = field(default_factory=list)


class Adapter:
    """Base class. Subclasses implement `_call` for a single attempt."""

    def __init__(self, model_id, label=None):
        self.model_id = model_id
        self.label = label or model_id

    def get_move(self, state, recent_moves):
        raise NotImplementedError


def validate_move(value):
    """Return the move if it is one of the 18 allowed moves, else None.

    Deliberately strict: no case folding or typographic-quote repair, so
    malformed output is counted instead of silently converted.
    """
    return value if isinstance(value, str) and value in MOVES else None
