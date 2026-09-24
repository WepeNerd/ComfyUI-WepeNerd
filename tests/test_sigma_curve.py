import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sigma_curve_node as sc  # noqa: E402

# Same fixture as tests/sigma_curve.test.mjs so the graph preview and output agree.
PARITY_POINTS = [[0, 1], [0.3, 0.2], [0.55, 0.25], [0.8, 0.02], [1, 0]]
PARITY = {
    "log smooth": [14.614642, 4.792817, 2.954795, 3.569847, 2.357055, 0.113735, 0, 0],
    "smooth": [14.614642, 6.515444, 2.958437, 3.577664, 2.265727, 0.174953, 0, 0],
    "linear": [14.614642, 8.119246, 3.020359, 3.507514, 2.085022, 0.243577, 0, 0],
}


class SigmaCurveMathTest(unittest.TestCase):
    def test_matches_frontend_parity_fixture(self):
        for mode, expected in PARITY.items():
            got = sc.sample_sigmas(PARITY_POINTS, 7, 14.614642, mode, True)
            self.assertEqual(len(got), len(expected))
            for g, e in zip(got, expected):
                self.assertAlmostEqual(g, e, places=5, msg=mode)

    def test_default_curve_is_a_karras_like_schedule(self):
        sigmas = sc.sample_sigmas(sc.parse_curve(sc.DEFAULT_CURVE), 10, 14.614642)
        self.assertEqual(len(sigmas), 11)
        self.assertAlmostEqual(sigmas[0], 14.614642, places=5)
        self.assertAlmostEqual(sigmas[-2], 0.0291675, places=3)
        self.assertEqual(sigmas[-1], 0.0)
        self.assertTrue(all(a > b for a, b in zip(sigmas, sigmas[1:])))

    def test_without_trailing_zero_samples_steps_plus_one(self):
        sigmas = sc.sample_sigmas([[0, 1], [1, 0.5]], 4, 2.0, "linear", False)
        self.assertEqual(sigmas, [2.0, 1.75, 1.5, 1.25, 1.0])

    def test_bad_curve_data_falls_back(self):
        self.assertEqual(sc.parse_curve("{oops"), [[0.0, 1.0], [1.0, 0.0]])
        self.assertEqual(sc.parse_curve(json.dumps({"points": [[0.5, 9]]})),
                         [[0.0, 1.0], [1.0, 1.0]])
        self.assertEqual(sc.normalize_points([[0.7, 0.1], [0.2, 0.6], [0.2, 0.3]]),
                         [[0.0, 0.6], [1.0, 0.1]])

    def test_node_outputs_float_tensor(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("torch unavailable")
        (sigmas,) = sc.WN_SigmaCurve().build(8, 1.0, 0.01, "smooth", True,
                                              '{"points":[[0,1],[1,0.01]]}')
        self.assertEqual(tuple(sigmas.shape), (9,))
        self.assertEqual(float(sigmas[-1]), 0.0)


if __name__ == "__main__":
    unittest.main()
