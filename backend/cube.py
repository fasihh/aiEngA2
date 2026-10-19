"""Deterministic 3x3 cube engine on a 54-facelet string.

Facelet order is the Kociemba convention: U(0-8) R(9-17) F(18-26) D(27-35)
L(36-44) B(45-53), each face read row by row as seen from outside the cube.
The solved state is "UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB".

Moves are the 18 half-turn-metric moves: each face with no suffix (90 degrees
clockwise seen from outside), "'" (counterclockwise) and "2" (180 degrees).
Permutations are derived from sticker geometry, so there are no hand-typed tables.
"""
import random

FACES = "URFDLB"
SOLVED = "".join(f * 9 for f in FACES)
MOVES = [f + s for f in FACES for s in ("", "'", "2")]

_NORMALS = {
    "U": (0, 1, 0), "R": (1, 0, 0), "F": (0, 0, 1),
    "D": (0, -1, 0), "L": (-1, 0, 0), "B": (0, 0, -1),
}


def _sticker_coords():
    """(position, normal) for each of the 54 facelets, in facelet order."""
    out = []
    for r in range(3):
        for c in range(3):
            out.append(((c - 1, 1, r - 1), _NORMALS["U"]))
    for r in range(3):
        for c in range(3):
            out.append(((1, 1 - r, 1 - c), _NORMALS["R"]))
    for r in range(3):
        for c in range(3):
            out.append(((c - 1, 1 - r, 1), _NORMALS["F"]))
    for r in range(3):
        for c in range(3):
            out.append(((c - 1, -1, 1 - r), _NORMALS["D"]))
    for r in range(3):
        for c in range(3):
            out.append(((-1, 1 - r, c - 1), _NORMALS["L"]))
    for r in range(3):
        for c in range(3):
            out.append(((1 - c, 1 - r, -1), _NORMALS["B"]))
    return out


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _rot_cw(v, n):
    """Rotate v by -90 degrees about axis n (clockwise seen from outside along n)."""
    c = _cross(n, v)
    d = _dot(n, v)
    return (-c[0] + n[0] * d, -c[1] + n[1] * d, -c[2] + n[2] * d)


def _build_perms():
    stickers = _sticker_coords()
    index = {s: i for i, s in enumerate(stickers)}
    perms = {}
    for face, n in _NORMALS.items():
        # src[dest] = index of the sticker that moves to dest.
        src = list(range(54))
        for i, (pos, nor) in enumerate(stickers):
            if _dot(pos, n) == 1:
                src[index[(_rot_cw(pos, n), _rot_cw(nor, n))]] = i
        perms[face] = src
    return perms


def _compose(p, q):
    """Apply p then q, as a source-index permutation."""
    return [p[q[i]] for i in range(54)]


def _build_move_table():
    base = _build_perms()
    table = {}
    for f, p in base.items():
        p2 = _compose(p, p)
        p3 = _compose(p2, p)
        table[f] = tuple(p)
        table[f + "2"] = tuple(p2)
        table[f + "'"] = tuple(p3)
    return table


_TABLE = _build_move_table()


def apply_move(state, move):
    """Return the state after one move. Raises ValueError for an unknown move."""
    try:
        perm = _TABLE[move]
    except KeyError:
        raise ValueError(f"invalid move: {move!r}") from None
    return "".join(state[i] for i in perm)


def apply_moves(state, moves):
    for m in moves:
        state = apply_move(state, m)
    return state


def is_solved(state):
    return state == SOLVED


def is_valid_move(move):
    return move in _TABLE


def inverse_move(move):
    if move.endswith("2"):
        return move
    return move[0] if move.endswith("'") else move + "'"


def inverse_sequence(moves):
    return [inverse_move(m) for m in reversed(moves)]


def scramble_moves(n, seed=None):
    """n random moves, never turning the same face twice in a row."""
    rng = random.Random(seed)
    out, last = [], None
    while len(out) < n:
        m = rng.choice(MOVES)
        if m[0] == last:
            continue
        out.append(m)
        last = m[0]
    return out


def scramble(n, seed=None):
    """Return (state, moves) for a reproducible random scramble of n moves."""
    moves = scramble_moves(n, seed)
    return apply_moves(SOLVED, moves), moves


def validate_state(state):
    """Cheap sanity check: 54 chars, nine of each colour."""
    return len(state) == 54 and all(state.count(f) == 9 for f in FACES)
