import unittest

from backend.cube import (
    FACES, MOVES, SOLVED, apply_move, apply_moves, inverse_sequence, is_solved,
    scramble, validate_state,
)
from backend.solver import optimal_distance, solve_optimal


class CubeTests(unittest.TestCase):
    def test_solved(self):
        self.assertTrue(is_solved(SOLVED))
        self.assertEqual(len(SOLVED), 54)

    def test_move_count(self):
        self.assertEqual(len(MOVES), 18)

    def test_four_quarter_turns_identity(self):
        for f in FACES:
            s = SOLVED
            for _ in range(4):
                s = apply_move(s, f)
            self.assertEqual(s, SOLVED)

    def test_inverse_and_double(self):
        base = scramble(10, seed=1)[0]
        for f in FACES:
            self.assertEqual(apply_moves(base, [f, f + "'"]), base)
            self.assertEqual(apply_moves(base, [f, f]), apply_move(base, f + "2"))

    def test_u_turn_geometry(self):
        # Clockwise U moves the front top row onto the left face.
        s = apply_move(SOLVED, "U")
        self.assertEqual(s[36:39], "FFF")   # L top row
        self.assertEqual(s[45:48], "LLL")   # B top row
        self.assertEqual(s[9:12], "BBB")    # R top row
        self.assertEqual(s[18:21], "RRR")   # F top row

    def test_sexy_move_period_is_six(self):
        seq = ["R", "U", "R'", "U'"]
        s = SOLVED
        for _ in range(6):
            s = apply_moves(s, seq)
        self.assertEqual(s, SOLVED)
        self.assertNotEqual(apply_moves(SOLVED, seq * 5), SOLVED)

    def test_scramble_then_reverse_is_solved(self):
        for seed in range(20):
            state, moves = scramble(15, seed)
            self.assertTrue(validate_state(state))
            self.assertTrue(is_solved(apply_moves(state, inverse_sequence(moves))))

    def test_scramble_reproducible(self):
        self.assertEqual(scramble(5, 42), scramble(5, 42))

    def test_invalid_move(self):
        with self.assertRaises(ValueError):
            apply_move(SOLVED, "X")


class SolverTests(unittest.TestCase):
    def test_solved_distance_zero(self):
        self.assertEqual(optimal_distance(SOLVED), 0)

    def test_single_move(self):
        self.assertEqual(optimal_distance(apply_move(SOLVED, "R2")), 1)

    def test_known_distances(self):
        self.assertEqual(optimal_distance(apply_moves(SOLVED, ["R", "U"])), 2)
        self.assertEqual(optimal_distance(apply_moves(SOLVED, ["R", "U", "R'", "U'"])), 4)

    def test_solution_valid_and_not_longer_than_scramble(self):
        for seed in range(15):
            n = 3 + seed % 3  # 3..5
            state, moves = scramble(n, seed)
            sol = solve_optimal(state)
            self.assertTrue(is_solved(apply_moves(state, sol)))
            self.assertLessEqual(len(sol), n)


class LibraryTests(unittest.TestCase):
    """Cross-check the fast kernel in cube.py against the magiccube library."""

    def test_kernel_matches_magiccube(self):
        import random
        from backend.engine import CubeGame
        for seed in range(100):
            rnd = random.Random(seed)
            seq = [rnd.choice(MOVES) for _ in range(12)]
            game = CubeGame(scramble_len=0)
            for m in seq:
                game.apply(m)
            self.assertEqual(game.state, apply_moves(SOLVED, seq))

    def test_game_scramble_and_solve(self):
        from backend.engine import CubeGame
        g = CubeGame(scramble_len=5, seed=7)
        self.assertFalse(g.solved)
        self.assertEqual(g.initial_state, scramble(5, 7)[0])
        for m in solve_optimal(g.initial_state):
            g.apply(m)
        self.assertTrue(g.solved)
        with self.assertRaises(ValueError):
            g.apply("X")

    def test_reference_solution_solves(self):
        from backend.engine import reference_solution
        state, _ = scramble(18, 3)
        self.assertTrue(is_solved(apply_moves(state, reference_solution(state))))


if __name__ == "__main__":
    unittest.main()
