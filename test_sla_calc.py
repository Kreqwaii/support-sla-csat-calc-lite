import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sla_calc import (
    SlaTargets,
    analyze_tickets,
    load_tickets_csv,
    main,
    parse_tickets_stream,
    percentile,
    render_report,
)


SAMPLE_PATH = Path(__file__).parent / "sample_tickets.csv"


class TestSupportSlaCsatCalc(unittest.TestCase):
    def test_sample_csv_frt_mttr_and_sla_breach_rates(self) -> None:
        records = load_tickets_csv(SAMPLE_PATH)
        self.assertEqual(len(records), 20)

        report = analyze_tickets(records)
        s = report.summary

        self.assertEqual(s.total_tickets, 20)
        self.assertEqual(s.responded_tickets, 19)
        self.assertEqual(s.unanswered_tickets, 1)
        self.assertEqual(s.closed_tickets, 17)
        self.assertEqual(s.open_tickets, 3)

        self.assertAlmostEqual(s.frt_mean_min, 88.0, places=2)
        self.assertAlmostEqual(s.frt_p50_min, 60.0, places=2)
        self.assertAlmostEqual(s.frt_p95_min, 300.0, places=2)
        self.assertAlmostEqual(s.mttr_mean_min, 529.41, places=2)

        self.assertEqual(s.frt_breaches, 6)
        self.assertEqual(s.frt_evaluated, 19)
        self.assertAlmostEqual(s.frt_breach_rate_pct, 31.58, places=2)

        self.assertEqual(s.resolution_breaches, 4)
        self.assertEqual(s.resolution_evaluated, 17)
        self.assertAlmostEqual(s.resolution_breach_rate_pct, 23.53, places=2)

        self.assertEqual(s.overall_breaches, 7)
        self.assertEqual(s.overall_evaluated, 19)
        self.assertAlmostEqual(s.overall_sla_breach_rate_pct, 36.84, places=2)

        by_prio = {p.priority: p for p in report.by_priority}
        self.assertEqual(by_prio["urgent"].total_tickets, 5)
        self.assertAlmostEqual(by_prio["urgent"].frt_p50_min, 12.0, places=2)
        self.assertAlmostEqual(by_prio["urgent"].mttr_mean_min, 192.0, places=2)
        self.assertAlmostEqual(by_prio["urgent"].overall_sla_breach_rate_pct, 60.0, places=2)

        self.assertEqual(by_prio["high"].total_tickets, 7)
        self.assertAlmostEqual(by_prio["high"].frt_p50_min, 42.5, places=2)
        self.assertAlmostEqual(by_prio["high"].mttr_mean_min, 360.0, places=2)
        self.assertAlmostEqual(by_prio["high"].overall_sla_breach_rate_pct, 33.33, places=2)

        self.assertEqual(by_prio["normal"].total_tickets, 8)
        self.assertAlmostEqual(by_prio["normal"].frt_p50_min, 120.0, places=2)
        self.assertAlmostEqual(by_prio["normal"].mttr_mean_min, 891.43, places=2)
        self.assertAlmostEqual(by_prio["normal"].overall_sla_breach_rate_pct, 25.0, places=2)

    def test_sample_csv_csat_distribution_and_nss(self) -> None:
        records = load_tickets_csv(SAMPLE_PATH)
        report = analyze_tickets(records)
        c = report.csat

        self.assertEqual(c.rated_tickets, 16)
        self.assertEqual(c.unrated_tickets, 4)
        self.assertAlmostEqual(c.response_rate_pct, 80.0, places=2)
        self.assertAlmostEqual(c.mean_csat, 3.50, places=2)
        self.assertAlmostEqual(c.top2_satisfied_pct, 56.25, places=2)
        self.assertAlmostEqual(c.neutral_pct, 18.75, places=2)
        self.assertAlmostEqual(c.dissatisfied_pct, 25.00, places=2)
        self.assertAlmostEqual(c.net_satisfaction_score, 31.25, places=2)

        dist_by_score = {b.score: b for b in c.distribution}
        self.assertEqual(dist_by_score[5].count, 5)
        self.assertAlmostEqual(dist_by_score[5].share_pct, 31.25, places=2)
        self.assertEqual(dist_by_score[4].count, 4)
        self.assertEqual(dist_by_score[3].count, 3)
        self.assertEqual(dist_by_score[2].count, 2)
        self.assertEqual(dist_by_score[1].count, 2)

    def test_category_breakdown_and_flagged_tickets(self) -> None:
        records = load_tickets_csv(SAMPLE_PATH)
        report = analyze_tickets(records)

        by_cat = {cat.category: cat for cat in report.by_category}
        self.assertAlmostEqual(by_cat["onboarding"].overall_sla_breach_rate_pct, 0.0, places=2)
        self.assertAlmostEqual(by_cat["onboarding"].mean_csat, 5.0, places=2)
        self.assertAlmostEqual(by_cat["onboarding"].net_satisfaction_score, 100.0, places=2)

        self.assertAlmostEqual(by_cat["bug"].overall_sla_breach_rate_pct, 66.67, places=2)
        self.assertAlmostEqual(by_cat["bug"].net_satisfaction_score, -50.0, places=2)

        flagged_ids = [t.ticket_id for t in report.flagged_tickets]
        self.assertEqual(
            flagged_ids,
            ["T-1003", "T-1004", "T-1005", "T-1008", "T-1010", "T-1016", "T-1019"],
        )

    def test_custom_sla_thresholds_and_as_of_open_breaches(self) -> None:
        records = load_tickets_csv(SAMPLE_PATH)
        strict_targets = SlaTargets(
            frt_minutes={"urgent": 5.0, "high": 30.0, "normal": 90.0},
            resolution_minutes={"urgent": 120.0, "high": 240.0, "normal": 600.0},
        )
        as_of = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        report = analyze_tickets(records, sla_targets=strict_targets, as_of=as_of)

        # T-1012 (high, created 10:00Z, unanswered) is 120m old at 12:00Z (> 30m FRT SLA)
        self.assertEqual(report.summary.overall_evaluated, 20)
        flagged_by_id = {t.ticket_id: t for t in report.flagged_tickets}
        self.assertIn("T-1012", flagged_by_id)
        self.assertTrue(flagged_by_id["T-1012"].frt_breached)

    def test_percentile_and_unrated_single_ticket_edge_cases(self) -> None:
        self.assertIsNone(percentile([], 50.0))
        self.assertEqual(percentile([15.0], 95.0), 15.0)
        self.assertAlmostEqual(percentile([10.0, 20.0, 30.0, 40.0], 50.0), 25.0)

        csv_text = (
            "ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score\n"
            "X-1,,normal,2026-11-18T10:00:00Z,2026-11-18T10:15:00Z,,\n"
        )
        records = parse_tickets_stream(io.StringIO(csv_text))
        self.assertEqual(records[0].category, "uncategorized")
        report = analyze_tickets(records)
        self.assertAlmostEqual(report.summary.frt_p50_min, 15.0, places=2)
        self.assertAlmostEqual(report.summary.frt_p95_min, 15.0, places=2)
        self.assertIsNone(report.summary.mttr_mean_min)
        self.assertIsNone(report.csat.mean_csat)
        self.assertIsNone(report.csat.net_satisfaction_score)

    def test_timezone_normalization_and_validation_errors(self) -> None:
        # Mixed +03:00 and Z timestamps: 12:00+03:00 == 09:00Z -> FRT = 20m
        valid_csv = (
            "ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score\n"
            "TZ-1,billing,URGENT,2026-11-18T12:00:00+03:00,2026-11-18T09:20:00Z,2026-11-18T10:00:00Z,4\n"
        )
        recs = parse_tickets_stream(io.StringIO(valid_csv))
        self.assertAlmostEqual(recs[0].frt_minutes, 20.0, places=2)
        self.assertAlmostEqual(recs[0].resolution_minutes, 60.0, places=2)

        bad_cases = [
            # Invalid priority
            "ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score\n"
            "E-1,bug,critical,2026-11-18T10:00:00Z,,,\n",
            # Out-of-range CSAT
            "ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score\n"
            "E-2,bug,high,2026-11-18T10:00:00Z,,,6\n",
            # Negative FRT
            "ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score\n"
            "E-3,bug,high,2026-11-18T10:00:00Z,2026-11-18T09:50:00Z,,\n",
            # Missing column
            "ticket_id,priority,created_at\nE-4,high,2026-11-18T10:00:00Z\n",
        ]
        for bad_csv in bad_cases:
            with self.assertRaises(ValueError):
                parse_tickets_stream(io.StringIO(bad_csv))

    def test_json_csv_markdown_rendering_and_cli_file_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            json_out = Path(tmpdir) / "report.json"
            csv_out = Path(tmpdir) / "report.csv"

            rc_json = main(
                [str(SAMPLE_PATH), "--format", "json", "--output", str(json_out)]
            )
            self.assertEqual(rc_json, 0)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["total_tickets"], 20)
            self.assertAlmostEqual(payload["csat"]["net_satisfaction_score"], 31.25, places=2)
            self.assertEqual(len(payload["by_priority"]), 3)

            rc_csv = main(
                [str(SAMPLE_PATH), "--format", "csv", "--output", str(csv_out)]
            )
            self.assertEqual(rc_csv, 0)
            with csv_out.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            sections = {row["section"] for row in rows}
            self.assertEqual(
                sections, {"overall", "priority", "category", "csat_distribution"}
            )

            report = analyze_tickets(load_tickets_csv(SAMPLE_PATH))
            md = render_report(report, "markdown")
            self.assertIn("# Support Ticket SLA, FRT & CSAT Analytics Report", md)
            self.assertIn("Net Satisfaction Score (NSS)", md)

    def test_cli_stdin_and_missing_file_error(self) -> None:
        sample_text = SAMPLE_PATH.read_text(encoding="utf-8")
        stdout_buf = io.StringIO()
        with patch("sys.stdin", io.StringIO(sample_text)), patch(
            "sys.stdout", stdout_buf
        ):
            rc = main(["-", "--format", "json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout_buf.getvalue())
        self.assertEqual(parsed["summary"]["closed_tickets"], 17)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            rc_err = main(["does_not_exist_12345.csv"])
        self.assertEqual(rc_err, 1)
        self.assertIn("Input CSV not found", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
