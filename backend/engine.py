"""Game-facing cube engine built on the `magiccube` library.

magiccube is the authoritative cube; `cube.py` (pure-Python facelet permutations)
is only a fast kernel for the BFS solver and is cross-checked against magiccube
in the tests. State is exposed as the 54-char Kociemba facelet string in
URFDLB letters (see cube.py), which is what the models receive.
"""
import kociemba
import magiccube

from .cube import MOVES, SOLVED, scramble_moves

# magiccube names faces by colour; map to the face letter of the same centre.
_COLOR_TO_FACE = {"W": "U", "R": "R", "G": "F", "Y": "D", "O": "L", "B": "B"}


def _to_facelets(cube):
    return "".join(_COLOR_TO_FACE[c] for c in cube.get_kociemba_facelet_colors())


class CubeGame:
    """One puzzle: a scrambled cube that moves are applied to."""

    def __init__(self, scramble_len=4, seed=None):
        self.cube = magiccube.Cube(3)
        self.scramble = scramble_moves(scramble_len, seed)
        self.cube.rotate(" ".join(self.scramble))
        self.initial_state = self.state
        self.moves = []

    @property
    def state(self):
        return _to_facelets(self.cube)

    @property
    def solved(self):
        return self.cube.is_done()

    def apply(self, move):
        """Apply one move from MOVES; raises ValueError otherwise."""
        if move not in MOVES:
            raise ValueError(f"invalid move: {move!r}")
        self.cube.rotate(move)
        self.moves.append(move)
        return self.state


def reference_solution(state):
    """Near-optimal solution via Kociemba's two-phase algorithm (not guaranteed shortest).

    Use backend.solver.solve_optimal for exact distances on small puzzles; this
    is the reference for the 15-20 move extension where BFS is infeasible.
    """
    if state == SOLVED:
        return []
    return kociemba.solve(state).split()
