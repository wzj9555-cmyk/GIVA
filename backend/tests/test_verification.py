"""Small regression tests for offline dataset selection and reference math."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import verify_raster as verification


class VerificationSupportTests(unittest.TestCase):
    def test_independent_cosine_handles_zero_opposite_and_orthogonal_vectors(self):
        vectors = np.array([[1, 0], [0, 1], [-1, 0], [1, 1], [0, 0]], dtype=np.float32)
        before = vectors.copy()
        scores = verification.independent_cosine_scores(vectors, 0)
        self.assertEqual(scores.dtype, np.dtype("float64"))
        self.assertEqual(scores[0], -np.inf)
        self.assertEqual(scores[4], -np.inf)
        np.testing.assert_allclose(scores[1:4], [0, -1, 1 / np.sqrt(2)], atol=1e-14)
        np.testing.assert_array_equal(vectors, before)
        with self.assertRaisesRegex(ValueError, "非零向量"):
            verification.independent_cosine_scores(vectors, 4)

    def test_selects_all_direct_children_but_not_temporary_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("joint", "concat", ".joint.pending"):
                (root / name).mkdir()
            with patch.object(verification, "read_cache_metadata", return_value={}):
                selected = verification.select_directories(root)
                self.assertEqual([path.name for path in selected], ["concat", "joint"])
                self.assertEqual(verification.select_directories(root, "joint"), [root / "joint"])
                with self.assertRaisesRegex(ValueError, "不存在"):
                    verification.select_directories(root, "missing")

    def test_incomplete_cache_fails_instead_of_silently_skipping_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "joint").mkdir()
            with patch.object(verification, "read_cache_metadata", return_value=None):
                with self.assertRaisesRegex(ValueError, "不完整"):
                    verification.select_directories(root)

    def test_empty_cache_reports_how_to_prepare_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "start.command"):
                verification.select_directories(Path(temporary))


if __name__ == "__main__":
    unittest.main()
