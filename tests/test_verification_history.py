import sys
import tempfile
import unittest
from datetime import datetime
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

    def test_old_six_lead_vintage_is_not_treated_as_complete(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "verification_history.py").read_text(encoding="utf-8")
        self.assertIn('previous_vintage.get("lead_days") == list(LEADS)', source)

    def test_external_providers_are_isolated_from_core_weather_variables(self):
        source = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("const rainPairs=pairs.concat(Object.entries(externalRainRows)", source)
        self.assertIn("const rs=pairs.map(x=>x.row)", source)
        self.assertIn("temp:weightedTemp===null?get('temp'):weightedTemp", source)
        self.assertIn("wind:weightedWind===null?get('wind'):weightedWind", source)
        self.assertIn("gust:weightedGust===null?get('gust'):weightedGust", source)
        self.assertIn("const amountCandidate=courseWeights||globalAmountWeights", source)
        self.assertIn("const weightedTemp=weightedRainValue(pairs,variableWeights.temperature_c", source)
        self.assertIn("const weightedWind=weightedRainValue(pairs,variableWeights.wind_speed_kmh", source)
        self.assertIn("externalRainRows,variableWeights", source)
        self.assertIn("const weightDoc=await loadVerificationWeights()", source)
        self.assertIn('weightDoc?.variables?.[variable]?.lead_days?.[leadKey]', source)

    def test_forecast_archive_contains_weather_variables_and_vc_hour_alignment(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "verification_history.py").read_text(encoding="utf-8")
        self.assertIn('"temp_c": r.get("temp")', source)
        self.assertIn('"wind_kmh": r.get("wind")', source)
        self.assertIn('"humidity_pct": r.get("rh")', source)
        self.assertIn('"temperature_c": weather.get("temperature_c")', source)
        self.assertIn('def build_actual_weather_hourly(now: datetime):', source)
        self.assertIn('weather_time = str(row.get("weather_time") or row.get("time") or "")', source)
        self.assertIn('f"{(int(str(r.get(\'time\', \'\'))[11:13]) + 1):02d}:00"', source)

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

    def test_continuous_score_reports_mae_rmse_bias_and_vintages(self):
        score = vh.ContinuousScore()
        score.add(20.0, 22.0, "v1")
        score.add(25.0, 24.0, "v2")
        result = score.result()
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["distinct_vintage_count"], 2)
        self.assertEqual(result["MAE"], 1.5)
        self.assertEqual(result["Bias"], -0.5)
        self.assertAlmostEqual(result["RMSE"], (2.5 ** 0.5), places=3)

    def test_continuous_weights_use_only_qualified_core_models(self):
        rows = {
            "ECMWF IFS": {"sample_count": 250, "distinct_vintage_count": 25, "MAE": 1.0},
            "NOAA GFS": {"sample_count": 250, "distinct_vintage_count": 25, "MAE": 1.5},
            "DWD ICON": {"sample_count": 250, "distinct_vintage_count": 25, "MAE": 2.0},
            "Visual Crossing": {"sample_count": 500, "distinct_vintage_count": 40, "MAE": 0.1},
        }
        weights = vh.normalized_continuous_weights(rows, 200, 20, 2000)
        self.assertEqual(set(weights), {"ECMWF IFS", "NOAA GFS", "DWD ICON"})
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)
        self.assertTrue(all(value <= 0.35 for value in weights.values()))

    def test_continuous_weights_reject_insufficient_samples_or_vintages(self):
        rows = {
            name: {"sample_count": 199, "distinct_vintage_count": 25, "MAE": 1.0}
            for name in vh.CORE_MODEL_SOURCES[:3]
        }
        self.assertIsNone(vh.normalized_continuous_weights(rows, 200, 20, 2000))
        for row in rows.values():
            row["sample_count"] = 250
            row["distinct_vintage_count"] = 19
        self.assertIsNone(vh.normalized_continuous_weights(rows, 200, 20, 2000))

    def test_cwa_weather_observations_are_hour_aligned_and_wind_converted(self):
        original_root = vh.ROOT
        with tempfile.TemporaryDirectory() as temp_dir:
            vh.ROOT = Path(temp_dir)
            obs_dir = vh.ROOT / "observations"
            obs_dir.mkdir()
            snapshot = {
                "snapshot_schema_version": 2,
                "courses": {
                    "TWG001": {
                        "course_name": "Test Course",
                        "weather_station_code": "466900",
                        "weather_station_observed_at": "2026-11-27T02:00:00+00:00",
                        "weather_station_distance_km": 5.0,
                        "weather_station_quality_status": "passed",
                        "weather_station_observation_age_minutes": 10,
                        "temperature_c": 22.0,
                        "relative_humidity_pct": 60,
                        "wind_speed_ms": 2.0,
                        "wind_gust_ms": 3.0,
                        "wind_direction_deg": 150,
                    }
                },
            }
            vh.write_gzip_jsonl(obs_dir / "2026-11-27.jsonl.gz", [snapshot])
            actual = vh.build_actual_weather_hourly(
                datetime(2026, 11, 28, 12, 0, tzinfo=vh.TZ)
            )
            row = actual[vh.name_key("Test Course")][("2026-11-27", 10)]
            self.assertEqual(row["temperature_c"], 22.0)
            self.assertEqual(row["relative_humidity_pct"], 60)
            self.assertAlmostEqual(row["wind_speed_kmh"], 7.2, places=2)
            self.assertAlmostEqual(row["wind_gust_kmh"], 10.8, places=2)
        vh.ROOT = original_root

    def test_stale_or_distant_weather_station_is_excluded(self):
        original_root = vh.ROOT
        with tempfile.TemporaryDirectory() as temp_dir:
            vh.ROOT = Path(temp_dir)
            obs_dir = vh.ROOT / "observations"
            obs_dir.mkdir()
            base = {
                "course_name": "Test Course",
                "weather_station_code": "466900",
                "weather_station_observed_at": "2026-11-27T02:00:00+00:00",
                "weather_station_distance_km": 5.0,
                "weather_station_quality_status": "passed",
                "weather_station_observation_age_minutes": 120,
                "temperature_c": 22.0,
                "relative_humidity_pct": 60,
                "wind_speed_ms": 2.0,
                "wind_gust_ms": None,
            }
            snapshot = {"snapshot_schema_version": 2, "courses": {"TWG001": base}}
            vh.write_gzip_jsonl(obs_dir / "2026-11-27.jsonl.gz", [snapshot])
            actual = vh.build_actual_weather_hourly(
                datetime(2026, 11, 28, 12, 0, tzinfo=vh.TZ)
            )
            self.assertEqual(actual, {})
        vh.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
