import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verification_history as vh


class ForecastVerificationGateTests(unittest.TestCase):
    def eligible_rows(self, **overrides):
        base = {
            "sample_count": 250,
            "wet_sample_count": 25,
            "distinct_vintage_count": 25,
            "Weight_error_mm": 1.2,
            "probability_sample_count": 250,
            "probability_wet_sample_count": 25,
            "probability_vintage_count": 25,
            "Brier_score": 0.12,
        }
        base.update(overrides)
        return {f"source-{i}": dict(base) for i in range(3)}

    def test_all_sixteen_lead_days_are_archived(self):
        self.assertEqual(tuple(range(1, 17)), vh.LEADS)

    def test_dry_only_pairs_never_activate_amount_weights(self):
        rows = self.eligible_rows(wet_sample_count=0, Weight_error_mm=None)
        self.assertIsNone(
            vh.normalized_weights(rows, "Weight_error_mm", vh.MIN_GLOBAL_PAIRS, 2000)
        )

    def test_probability_weights_require_wet_probability_samples(self):
        rows = self.eligible_rows(probability_wet_sample_count=0)
        self.assertIsNone(
            vh.normalized_weights(rows, "Brier_score", vh.MIN_GLOBAL_PAIRS, 2000)
        )

    def test_probability_weights_require_their_own_vintage_coverage(self):
        rows = self.eligible_rows(probability_vintage_count=19)
        self.assertIsNone(
            vh.normalized_weights(rows, "Brier_score", vh.MIN_GLOBAL_PAIRS, 2000)
        )

    def test_amount_weights_require_distinct_forecast_vintages(self):
        rows = self.eligible_rows(distinct_vintage_count=19)
        self.assertIsNone(
            vh.normalized_weights(rows, "Weight_error_mm", vh.MIN_GLOBAL_PAIRS, 2000)
        )

    def test_valid_weights_are_normalized_and_capped(self):
        rows = self.eligible_rows()
        weights = vh.normalized_weights(
            rows, "Weight_error_mm", vh.MIN_GLOBAL_PAIRS, 2000
        )
        self.assertIsNotNone(weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)
        self.assertTrue(all(value <= 0.35 for value in weights.values()))

    def test_probability_wet_count_only_uses_rows_with_probability(self):
        score = vh.Score()
        score.add(0.0, 0.0, 30, "2026-11-01")
        score.add(0.0, 0.5, None, "2026-11-02")
        result = score.result()
        self.assertEqual(result["wet_sample_count"], 1)
        self.assertEqual(result["probability_wet_sample_count"], 0)
        self.assertEqual(result["probability_sample_count"], 1)
        self.assertEqual(result["distinct_vintage_count"], 2)
        self.assertEqual(result["probability_vintage_count"], 1)


if __name__ == "__main__":
    unittest.main()
