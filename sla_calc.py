#!/usr/bin/env python3
"""
Support Ticket SLA, First-Response Time & CSAT Analytics CLI (Lite)
100% Python 3.9+ standard library — zero external dependencies.

Reads a CSV export of support tickets
(ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score)
and computes:
- First-Response Time (FRT) p50 / p95 / mean (in minutes)
- Mean Time to Resolution (MTTR) plus p50 / p95 resolution times
- SLA Breach Rate by priority (urgent / high / normal) and overall
- CSAT 1-5 score distribution, mean CSAT, Top-2-Box %, and Net Satisfaction Score
- Category breakdowns and breached / low-CSAT ticket action list
- Markdown, JSON, and CSV output formats
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple


REQUIRED_COLUMNS = (
    "ticket_id",
    "category",
    "priority",
    "created_at",
    "first_response_at",
    "closed_at",
    "csat_score",
)

PRIORITIES = ("urgent", "high", "normal")
ALLOWED_PRIORITIES = frozenset(PRIORITIES)
EMPTY_MARKERS = frozenset({"", "na", "n/a", "null", "none", "-"})

DEFAULT_FRT_SLA_MINUTES: Dict[str, float] = {
    "urgent": 15.0,
    "high": 60.0,
    "normal": 240.0,
}

DEFAULT_RESOLUTION_SLA_MINUTES: Dict[str, float] = {
    "urgent": 240.0,
    "high": 480.0,
    "normal": 1440.0,
}


@dataclass(frozen=True)
class SlaTargets:
    frt_minutes: Dict[str, float]
    resolution_minutes: Dict[str, float]

    @classmethod
    def default(cls) -> "SlaTargets":
        return cls(
            frt_minutes=dict(DEFAULT_FRT_SLA_MINUTES),
            resolution_minutes=dict(DEFAULT_RESOLUTION_SLA_MINUTES),
        )


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    category: str
    priority: str
    created_at: datetime
    first_response_at: Optional[datetime]
    closed_at: Optional[datetime]
    csat_score: Optional[int]

    @property
    def frt_minutes(self) -> Optional[float]:
        if self.first_response_at is None:
            return None
        return (self.first_response_at - self.created_at).total_seconds() / 60.0

    @property
    def resolution_minutes(self) -> Optional[float]:
        if self.closed_at is None:
            return None
        return (self.closed_at - self.created_at).total_seconds() / 60.0


@dataclass(frozen=True)
class EvaluatedTicket:
    ticket_id: str
    category: str
    priority: str
    created_at: str
    frt_minutes: Optional[float]
    resolution_minutes: Optional[float]
    frt_sla_target_min: float
    resolution_sla_target_min: float
    frt_evaluated: bool
    frt_breached: bool
    resolution_evaluated: bool
    resolution_breached: bool
    overall_evaluated: bool
    overall_breached: bool
    csat_score: Optional[int]
    status: str
    breach_reasons: List[str]


@dataclass(frozen=True)
class PrioritySlaStats:
    priority: str
    total_tickets: int
    responded_tickets: int
    closed_tickets: int
    frt_sla_target_min: float
    resolution_sla_target_min: float
    frt_p50_min: Optional[float]
    frt_p95_min: Optional[float]
    mttr_mean_min: Optional[float]
    frt_evaluated: int
    frt_breaches: int
    frt_breach_rate_pct: Optional[float]
    resolution_evaluated: int
    resolution_breaches: int
    resolution_breach_rate_pct: Optional[float]
    overall_evaluated: int
    overall_breaches: int
    overall_sla_breach_rate_pct: Optional[float]


@dataclass(frozen=True)
class CsatBucket:
    score: int
    label: str
    count: int
    share_pct: float


@dataclass(frozen=True)
class CsatSummary:
    rated_tickets: int
    unrated_tickets: int
    response_rate_pct: float
    mean_csat: Optional[float]
    top2_satisfied_count: int
    top2_satisfied_pct: Optional[float]
    neutral_count: int
    neutral_pct: Optional[float]
    dissatisfied_count: int
    dissatisfied_pct: Optional[float]
    net_satisfaction_score: Optional[float]
    distribution: List[CsatBucket]


@dataclass(frozen=True)
class CategoryStats:
    category: str
    total_tickets: int
    responded_tickets: int
    closed_tickets: int
    frt_p50_min: Optional[float]
    frt_p95_min: Optional[float]
    mttr_mean_min: Optional[float]
    overall_sla_breach_rate_pct: Optional[float]
    rated_tickets: int
    mean_csat: Optional[float]
    net_satisfaction_score: Optional[float]


@dataclass(frozen=True)
class AnalyticsSummary:
    as_of: Optional[str]
    total_tickets: int
    responded_tickets: int
    unanswered_tickets: int
    closed_tickets: int
    open_tickets: int
    frt_mean_min: Optional[float]
    frt_p50_min: Optional[float]
    frt_p95_min: Optional[float]
    mttr_mean_min: Optional[float]
    mttr_p50_min: Optional[float]
    mttr_p95_min: Optional[float]
    frt_evaluated: int
    frt_breaches: int
    frt_breach_rate_pct: Optional[float]
    resolution_evaluated: int
    resolution_breaches: int
    resolution_breach_rate_pct: Optional[float]
    overall_evaluated: int
    overall_breaches: int
    overall_sla_breach_rate_pct: Optional[float]


@dataclass(frozen=True)
class SupportAnalyticsReport:
    summary: AnalyticsSummary
    sla_targets: SlaTargets
    by_priority: List[PrioritySlaStats]
    csat: CsatSummary
    by_category: List[CategoryStats]
    flagged_tickets: List[EvaluatedTicket]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_timestamp(raw: str, field_name: str, row_num: int) -> datetime:
    value = raw.strip()
    if not value:
        raise ValueError(f"Row {row_num}: missing required timestamp '{field_name}'.")
    if value.endswith("Z") or value.endswith("z"):
        value = value[:-1] + "+00:00"
    try:
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            parsed_date = date.fromisoformat(value)
            return datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                tzinfo=timezone.utc,
            )
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Row {row_num}: invalid ISO timestamp '{raw}' for '{field_name}'."
        ) from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_optional_timestamp(
    raw: str, field_name: str, row_num: int
) -> Optional[datetime]:
    cleaned = raw.strip()
    if cleaned.lower() in EMPTY_MARKERS:
        return None
    return parse_timestamp(cleaned, field_name, row_num)


def parse_optional_csat(raw: str, row_num: int) -> Optional[int]:
    cleaned = raw.strip()
    if cleaned.lower() in EMPTY_MARKERS:
        return None
    try:
        score = int(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Row {row_num}: invalid csat_score '{raw}' (expected integer 1-5)."
        ) from exc
    if score < 1 or score > 5:
        raise ValueError(
            f"Row {row_num}: csat_score must be between 1 and 5 (got {score})."
        )
    return score


def parse_tickets_stream(stream: TextIO) -> List[TicketRecord]:
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise ValueError("CSV file is empty or missing a header row.")

    normalized_fields = [col.strip() for col in reader.fieldnames]
    reader.fieldnames = normalized_fields
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in normalized_fields]
    if missing_cols:
        raise ValueError(
            f"Missing required CSV columns: {', '.join(missing_cols)}. "
            f"Expected: {', '.join(REQUIRED_COLUMNS)}"
        )

    records: List[TicketRecord] = []
    for row_num, row in enumerate(reader, start=2):
        if row is None or all(
            (v is None or v.strip() == "") for v in row.values()
        ):
            continue

        ticket_id = (row.get("ticket_id") or "").strip()
        if not ticket_id:
            raise ValueError(f"Row {row_num}: 'ticket_id' cannot be empty.")

        category_raw = (row.get("category") or "").strip()
        category = category_raw.lower() if category_raw else "uncategorized"

        priority = (row.get("priority") or "").strip().lower()
        if priority not in ALLOWED_PRIORITIES:
            raise ValueError(
                f"Row {row_num}: invalid priority '{row.get('priority')}'. "
                f"Allowed values: {', '.join(PRIORITIES)}"
            )

        created_at = parse_timestamp(
            row.get("created_at") or "", "created_at", row_num
        )
        first_response_at = parse_optional_timestamp(
            row.get("first_response_at") or "", "first_response_at", row_num
        )
        closed_at = parse_optional_timestamp(
            row.get("closed_at") or "", "closed_at", row_num
        )
        csat_score = parse_optional_csat(row.get("csat_score") or "", row_num)

        if first_response_at is not None and first_response_at < created_at:
            raise ValueError(
                f"Row {row_num} ({ticket_id}): first_response_at cannot be earlier than created_at."
            )
        if closed_at is not None and closed_at < created_at:
            raise ValueError(
                f"Row {row_num} ({ticket_id}): closed_at cannot be earlier than created_at."
            )

        records.append(
            TicketRecord(
                ticket_id=ticket_id,
                category=category,
                priority=priority,
                created_at=created_at,
                first_response_at=first_response_at,
                closed_at=closed_at,
                csat_score=csat_score,
            )
        )

    if not records:
        raise ValueError("CSV contains no ticket records.")

    return records


def load_tickets_csv(csv_path: Path) -> List[TicketRecord]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return parse_tickets_stream(fh)


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(float(v) for v in values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return sorted_vals[int(k)]
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * (k - lower)


def safe_mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def safe_rate_pct(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 2)


def round_opt(value: Optional[float], digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def compute_csat_summary(records: Sequence[TicketRecord]) -> CsatSummary:
    counts: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    rated_scores: List[int] = []
    for rec in records:
        if rec.csat_score is not None:
            counts[rec.csat_score] += 1
            rated_scores.append(rec.csat_score)

    total = len(records)
    rated = len(rated_scores)
    unrated = total - rated
    response_rate = round((rated / total) * 100.0, 2) if total > 0 else 0.0

    labels = {
        5: "Very Satisfied",
        4: "Satisfied",
        3: "Neutral",
        2: "Dissatisfied",
        1: "Very Dissatisfied",
    }

    distribution: List[CsatBucket] = []
    for score in (5, 4, 3, 2, 1):
        cnt = counts[score]
        share = round((cnt / rated) * 100.0, 2) if rated > 0 else 0.0
        distribution.append(
            CsatBucket(
                score=score,
                label=labels[score],
                count=cnt,
                share_pct=share,
            )
        )

    if rated == 0:
        return CsatSummary(
            rated_tickets=0,
            unrated_tickets=unrated,
            response_rate_pct=response_rate,
            mean_csat=None,
            top2_satisfied_count=0,
            top2_satisfied_pct=None,
            neutral_count=0,
            neutral_pct=None,
            dissatisfied_count=0,
            dissatisfied_pct=None,
            net_satisfaction_score=None,
            distribution=distribution,
        )

    top2_cnt = counts[4] + counts[5]
    neutral_cnt = counts[3]
    dissat_cnt = counts[1] + counts[2]

    top2_pct = (top2_cnt / rated) * 100.0
    neutral_pct = (neutral_cnt / rated) * 100.0
    dissat_pct = (dissat_cnt / rated) * 100.0
    nss = top2_pct - dissat_pct

    return CsatSummary(
        rated_tickets=rated,
        unrated_tickets=unrated,
        response_rate_pct=response_rate,
        mean_csat=round(sum(rated_scores) / rated, 2),
        top2_satisfied_count=top2_cnt,
        top2_satisfied_pct=round(top2_pct, 2),
        neutral_count=neutral_cnt,
        neutral_pct=round(neutral_pct, 2),
        dissatisfied_count=dissat_cnt,
        dissatisfied_pct=round(dissat_pct, 2),
        net_satisfaction_score=round(nss, 2),
        distribution=distribution,
    )


def evaluate_ticket(
    rec: TicketRecord,
    targets: SlaTargets,
    as_of: Optional[datetime],
) -> EvaluatedTicket:
    frt_target = targets.frt_minutes[rec.priority]
    res_target = targets.resolution_minutes[rec.priority]

    frt_min = rec.frt_minutes
    res_min = rec.resolution_minutes

    breach_reasons: List[str] = []

    if frt_min is not None:
        frt_evaluated = True
        frt_breached = frt_min > frt_target
        if frt_breached:
            breach_reasons.append(
                f"FRT {frt_min:.1f}m > {frt_target:.0f}m"
            )
    elif as_of is not None:
        elapsed_min = (as_of - rec.created_at).total_seconds() / 60.0
        if elapsed_min > frt_target:
            frt_evaluated = True
            frt_breached = True
            breach_reasons.append(
                f"Unanswered {elapsed_min:.1f}m > {frt_target:.0f}m FRT SLA"
            )
        else:
            frt_evaluated = False
            frt_breached = False
    else:
        frt_evaluated = False
        frt_breached = False

    if res_min is not None:
        res_evaluated = True
        res_breached = res_min > res_target
        if res_breached:
            breach_reasons.append(
                f"Resolution {res_min:.1f}m > {res_target:.0f}m"
            )
    elif as_of is not None:
        elapsed_min = (as_of - rec.created_at).total_seconds() / 60.0
        if elapsed_min > res_target:
            res_evaluated = True
            res_breached = True
            breach_reasons.append(
                f"Open {elapsed_min:.1f}m > {res_target:.0f}m Resolution SLA"
            )
        else:
            res_evaluated = False
            res_breached = False
    else:
        res_evaluated = False
        res_breached = False

    overall_evaluated = frt_evaluated or res_evaluated
    overall_breached = frt_breached or res_breached

    if rec.closed_at is not None:
        status = "closed"
    elif rec.first_response_at is not None:
        status = "open_responded"
    else:
        status = "open_unanswered"

    return EvaluatedTicket(
        ticket_id=rec.ticket_id,
        category=rec.category,
        priority=rec.priority,
        created_at=rec.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        frt_minutes=round_opt(frt_min),
        resolution_minutes=round_opt(res_min),
        frt_sla_target_min=frt_target,
        resolution_sla_target_min=res_target,
        frt_evaluated=frt_evaluated,
        frt_breached=frt_breached,
        resolution_evaluated=res_evaluated,
        resolution_breached=res_breached,
        overall_evaluated=overall_evaluated,
        overall_breached=overall_breached,
        csat_score=rec.csat_score,
        status=status,
        breach_reasons=breach_reasons,
    )


def analyze_tickets(
    records: Sequence[TicketRecord],
    sla_targets: Optional[SlaTargets] = None,
    as_of: Optional[datetime] = None,
) -> SupportAnalyticsReport:
    if not records:
        raise ValueError("Cannot analyze an empty list of tickets.")

    targets = sla_targets if sla_targets is not None else SlaTargets.default()
    evaluated = [evaluate_ticket(rec, targets, as_of) for rec in records]

    frt_vals = [rec.frt_minutes for rec in records if rec.frt_minutes is not None]
    res_vals = [
        rec.resolution_minutes for rec in records if rec.resolution_minutes is not None
    ]

    total = len(records)
    responded = len(frt_vals)
    unanswered = total - responded
    closed = len(res_vals)
    open_count = total - closed

    frt_eval_cnt = sum(1 for e in evaluated if e.frt_evaluated)
    frt_breach_cnt = sum(1 for e in evaluated if e.frt_breached)
    res_eval_cnt = sum(1 for e in evaluated if e.resolution_evaluated)
    res_breach_cnt = sum(1 for e in evaluated if e.resolution_breached)
    ov_eval_cnt = sum(1 for e in evaluated if e.overall_evaluated)
    ov_breach_cnt = sum(1 for e in evaluated if e.overall_breached)

    summary = AnalyticsSummary(
        as_of=as_of.strftime("%Y-%m-%dT%H:%M:%SZ") if as_of is not None else None,
        total_tickets=total,
        responded_tickets=responded,
        unanswered_tickets=unanswered,
        closed_tickets=closed,
        open_tickets=open_count,
        frt_mean_min=round_opt(safe_mean(frt_vals)),
        frt_p50_min=round_opt(percentile(frt_vals, 50.0)),
        frt_p95_min=round_opt(percentile(frt_vals, 95.0)),
        mttr_mean_min=round_opt(safe_mean(res_vals)),
        mttr_p50_min=round_opt(percentile(res_vals, 50.0)),
        mttr_p95_min=round_opt(percentile(res_vals, 95.0)),
        frt_evaluated=frt_eval_cnt,
        frt_breaches=frt_breach_cnt,
        frt_breach_rate_pct=safe_rate_pct(frt_breach_cnt, frt_eval_cnt),
        resolution_evaluated=res_eval_cnt,
        resolution_breaches=res_breach_cnt,
        resolution_breach_rate_pct=safe_rate_pct(res_breach_cnt, res_eval_cnt),
        overall_evaluated=ov_eval_cnt,
        overall_breaches=ov_breach_cnt,
        overall_sla_breach_rate_pct=safe_rate_pct(ov_breach_cnt, ov_eval_cnt),
    )

    by_priority: List[PrioritySlaStats] = []
    for prio in PRIORITIES:
        prio_recs = [r for r in records if r.priority == prio]
        prio_evals = [e for e in evaluated if e.priority == prio]
        p_frt = [r.frt_minutes for r in prio_recs if r.frt_minutes is not None]
        p_res = [
            r.resolution_minutes for r in prio_recs if r.resolution_minutes is not None
        ]

        p_frt_eval = sum(1 for e in prio_evals if e.frt_evaluated)
        p_frt_br = sum(1 for e in prio_evals if e.frt_breached)
        p_res_eval = sum(1 for e in prio_evals if e.resolution_evaluated)
        p_res_br = sum(1 for e in prio_evals if e.resolution_breached)
        p_ov_eval = sum(1 for e in prio_evals if e.overall_evaluated)
        p_ov_br = sum(1 for e in prio_evals if e.overall_breached)

        by_priority.append(
            PrioritySlaStats(
                priority=prio,
                total_tickets=len(prio_recs),
                responded_tickets=len(p_frt),
                closed_tickets=len(p_res),
                frt_sla_target_min=targets.frt_minutes[prio],
                resolution_sla_target_min=targets.resolution_minutes[prio],
                frt_p50_min=round_opt(percentile(p_frt, 50.0)),
                frt_p95_min=round_opt(percentile(p_frt, 95.0)),
                mttr_mean_min=round_opt(safe_mean(p_res)),
                frt_evaluated=p_frt_eval,
                frt_breaches=p_frt_br,
                frt_breach_rate_pct=safe_rate_pct(p_frt_br, p_frt_eval),
                resolution_evaluated=p_res_eval,
                resolution_breaches=p_res_br,
                resolution_breach_rate_pct=safe_rate_pct(p_res_br, p_res_eval),
                overall_evaluated=p_ov_eval,
                overall_breaches=p_ov_br,
                overall_sla_breach_rate_pct=safe_rate_pct(p_ov_br, p_ov_eval),
            )
        )

    csat_summary = compute_csat_summary(records)

    categories = sorted({r.category for r in records})
    by_category: List[CategoryStats] = []
    for cat in categories:
        cat_recs = [r for r in records if r.category == cat]
        cat_evals = [e for e in evaluated if e.category == cat]
        c_frt = [r.frt_minutes for r in cat_recs if r.frt_minutes is not None]
        c_res = [
            r.resolution_minutes for r in cat_recs if r.resolution_minutes is not None
        ]
        c_ov_eval = sum(1 for e in cat_evals if e.overall_evaluated)
        c_ov_br = sum(1 for e in cat_evals if e.overall_breached)
        cat_csat = compute_csat_summary(cat_recs)
        by_category.append(
            CategoryStats(
                category=cat,
                total_tickets=len(cat_recs),
                responded_tickets=len(c_frt),
                closed_tickets=len(c_res),
                frt_p50_min=round_opt(percentile(c_frt, 50.0)),
                frt_p95_min=round_opt(percentile(c_frt, 95.0)),
                mttr_mean_min=round_opt(safe_mean(c_res)),
                overall_sla_breach_rate_pct=safe_rate_pct(c_ov_br, c_ov_eval),
                rated_tickets=cat_csat.rated_tickets,
                mean_csat=cat_csat.mean_csat,
                net_satisfaction_score=cat_csat.net_satisfaction_score,
            )
        )
    by_category.sort(key=lambda c: (-c.total_tickets, c.category))

    flagged = [
        e
        for e in evaluated
        if e.overall_breached or (e.csat_score is not None and e.csat_score <= 2)
    ]

    return SupportAnalyticsReport(
        summary=summary,
        sla_targets=targets,
        by_priority=by_priority,
        csat=csat_summary,
        by_category=by_category,
        flagged_tickets=flagged,
    )


def fmt_minutes(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value < 60.0:
        return f"{value:.2f}m"
    hours = int(value // 60)
    rem_mins = value - (hours * 60)
    return f"{value:.2f}m ({hours}h {rem_mins:.0f}m)"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def fmt_num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def render_markdown(report: SupportAnalyticsReport) -> str:
    s = report.summary
    c = report.csat
    lines: List[str] = [
        "# Support Ticket SLA, FRT & CSAT Analytics Report",
        "",
        f"- **Total Tickets:** {s.total_tickets} "
        f"(Responded: {s.responded_tickets}, Unanswered: {s.unanswered_tickets}, "
        f"Closed: {s.closed_tickets}, Open: {s.open_tickets})",
    ]
    if s.as_of is not None:
        lines.append(f"- **As-Of Cutoff (Open SLA Check):** `{s.as_of}`")
    lines.extend(
        [
            "",
            "## Core Response, Resolution & CSAT Summary",
            "",
            "| Metric | Value |",
            "| :--- | ---: |",
            f"| First-Response Time (FRT) Mean | {fmt_minutes(s.frt_mean_min)} |",
            f"| **FRT p50 (Median)** | **{fmt_minutes(s.frt_p50_min)}** |",
            f"| **FRT p95** | **{fmt_minutes(s.frt_p95_min)}** |",
            f"| **Mean Time to Resolution (MTTR)** | **{fmt_minutes(s.mttr_mean_min)}** |",
            f"| Resolution p50 / p95 | {fmt_minutes(s.mttr_p50_min)} / {fmt_minutes(s.mttr_p95_min)} |",
            f"| FRT SLA Breach Rate | {fmt_pct(s.frt_breach_rate_pct)} ({s.frt_breaches}/{s.frt_evaluated}) |",
            f"| Resolution SLA Breach Rate | {fmt_pct(s.resolution_breach_rate_pct)} ({s.resolution_breaches}/{s.resolution_evaluated}) |",
            f"| **Overall SLA Breach Rate** | **{fmt_pct(s.overall_sla_breach_rate_pct)} ({s.overall_breaches}/{s.overall_evaluated})** |",
            f"| CSAT Survey Response Rate | {fmt_pct(c.response_rate_pct)} ({c.rated_tickets}/{s.total_tickets}) |",
            f"| Mean CSAT (1-5) | {fmt_num(c.mean_csat)} |",
            f"| CSAT Top-2-Box (4-5) | {fmt_pct(c.top2_satisfied_pct)} |",
            f"| **Net Satisfaction Score (NSS)** | **{fmt_num(c.net_satisfaction_score)}** |",
            "",
            "## SLA Breach Rate by Priority",
            "",
            "| Priority | Tickets | FRT Target | Res Target | FRT p50 | FRT p95 | MTTR | FRT Breach | Res Breach | Overall SLA Breach |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for p in report.by_priority:
        lines.append(
            f"| `{p.priority}` | {p.total_tickets} | "
            f"{p.frt_sla_target_min:.0f}m | {p.resolution_sla_target_min:.0f}m | "
            f"{fmt_num(p.frt_p50_min)}m | {fmt_num(p.frt_p95_min)}m | {fmt_num(p.mttr_mean_min)}m | "
            f"{fmt_pct(p.frt_breach_rate_pct)} ({p.frt_breaches}/{p.frt_evaluated}) | "
            f"{fmt_pct(p.resolution_breach_rate_pct)} ({p.resolution_breaches}/{p.resolution_evaluated}) | "
            f"**{fmt_pct(p.overall_sla_breach_rate_pct)}** ({p.overall_breaches}/{p.overall_evaluated}) |"
        )

    lines.extend(
        [
            "",
            "## CSAT Score Distribution (1-5) & Net Satisfaction Score",
            "",
            "| Score | Sentiment | Count | Share % |",
            "| :---: | :--- | ---: | ---: |",
        ]
    )
    for bucket in c.distribution:
        lines.append(
            f"| {bucket.score} | {bucket.label} | {bucket.count} | {bucket.share_pct:.2f}% |"
        )
    lines.extend(
        [
            "",
            f"- **NSS Formula:** `% Satisfied (4-5) - % Dissatisfied (1-2)` = "
            f"`{fmt_pct(c.top2_satisfied_pct)} - {fmt_pct(c.dissatisfied_pct)}` = "
            f"**{fmt_num(c.net_satisfaction_score)}**",
            "",
            "## Category Breakdown",
            "",
            "| Category | Tickets | FRT p50 | FRT p95 | MTTR | SLA Breach | Rated | Mean CSAT | NSS |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for cat in report.by_category:
        lines.append(
            f"| `{cat.category}` | {cat.total_tickets} | "
            f"{fmt_num(cat.frt_p50_min)}m | {fmt_num(cat.frt_p95_min)}m | {fmt_num(cat.mttr_mean_min)}m | "
            f"{fmt_pct(cat.overall_sla_breach_rate_pct)} | {cat.rated_tickets} | "
            f"{fmt_num(cat.mean_csat)} | {fmt_num(cat.net_satisfaction_score)} |"
        )

    if report.flagged_tickets:
        lines.extend(
            [
                "",
                "## Flagged Tickets (SLA Breached or CSAT <= 2)",
                "",
                "| Ticket ID | Priority | Category | FRT (min) | Res (min) | CSAT | Reason(s) |",
                "| :--- | :--- | :--- | ---: | ---: | :---: | :--- |",
            ]
        )
        for t in report.flagged_tickets:
            reasons = list(t.breach_reasons)
            if t.csat_score is not None and t.csat_score <= 2:
                reasons.append(f"Low CSAT ({t.csat_score}/5)")
            csat_str = str(t.csat_score) if t.csat_score is not None else "-"
            lines.append(
                f"| `{t.ticket_id}` | {t.priority} | {t.category} | "
                f"{fmt_num(t.frt_minutes)} | {fmt_num(t.resolution_minutes)} | "
                f"{csat_str} | {'; '.join(reasons)} |"
            )

    lines.append("")
    return "\n".join(lines)


def render_json(report: SupportAnalyticsReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def render_csv(report: SupportAnalyticsReport) -> str:
    buf = io.StringIO()
    fieldnames = [
        "section",
        "key",
        "total_tickets",
        "responded_tickets",
        "closed_tickets",
        "frt_p50_min",
        "frt_p95_min",
        "mttr_mean_min",
        "frt_breach_rate_pct",
        "resolution_breach_rate_pct",
        "overall_sla_breach_rate_pct",
        "rated_tickets",
        "mean_csat",
        "net_satisfaction_score",
        "count",
        "share_pct",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    s = report.summary
    c = report.csat
    writer.writerow(
        {
            "section": "overall",
            "key": "all",
            "total_tickets": s.total_tickets,
            "responded_tickets": s.responded_tickets,
            "closed_tickets": s.closed_tickets,
            "frt_p50_min": s.frt_p50_min,
            "frt_p95_min": s.frt_p95_min,
            "mttr_mean_min": s.mttr_mean_min,
            "frt_breach_rate_pct": s.frt_breach_rate_pct,
            "resolution_breach_rate_pct": s.resolution_breach_rate_pct,
            "overall_sla_breach_rate_pct": s.overall_sla_breach_rate_pct,
            "rated_tickets": c.rated_tickets,
            "mean_csat": c.mean_csat,
            "net_satisfaction_score": c.net_satisfaction_score,
            "count": "",
            "share_pct": "",
        }
    )

    for p in report.by_priority:
        writer.writerow(
            {
                "section": "priority",
                "key": p.priority,
                "total_tickets": p.total_tickets,
                "responded_tickets": p.responded_tickets,
                "closed_tickets": p.closed_tickets,
                "frt_p50_min": p.frt_p50_min,
                "frt_p95_min": p.frt_p95_min,
                "mttr_mean_min": p.mttr_mean_min,
                "frt_breach_rate_pct": p.frt_breach_rate_pct,
                "resolution_breach_rate_pct": p.resolution_breach_rate_pct,
                "overall_sla_breach_rate_pct": p.overall_sla_breach_rate_pct,
                "rated_tickets": "",
                "mean_csat": "",
                "net_satisfaction_score": "",
                "count": "",
                "share_pct": "",
            }
        )

    for cat in report.by_category:
        writer.writerow(
            {
                "section": "category",
                "key": cat.category,
                "total_tickets": cat.total_tickets,
                "responded_tickets": cat.responded_tickets,
                "closed_tickets": cat.closed_tickets,
                "frt_p50_min": cat.frt_p50_min,
                "frt_p95_min": cat.frt_p95_min,
                "mttr_mean_min": cat.mttr_mean_min,
                "frt_breach_rate_pct": "",
                "resolution_breach_rate_pct": "",
                "overall_sla_breach_rate_pct": cat.overall_sla_breach_rate_pct,
                "rated_tickets": cat.rated_tickets,
                "mean_csat": cat.mean_csat,
                "net_satisfaction_score": cat.net_satisfaction_score,
                "count": "",
                "share_pct": "",
            }
        )

    for b in c.distribution:
        writer.writerow(
            {
                "section": "csat_distribution",
                "key": str(b.score),
                "total_tickets": "",
                "responded_tickets": "",
                "closed_tickets": "",
                "frt_p50_min": "",
                "frt_p95_min": "",
                "mttr_mean_min": "",
                "frt_breach_rate_pct": "",
                "resolution_breach_rate_pct": "",
                "overall_sla_breach_rate_pct": "",
                "rated_tickets": c.rated_tickets,
                "mean_csat": c.mean_csat,
                "net_satisfaction_score": c.net_satisfaction_score,
                "count": b.count,
                "share_pct": b.share_pct,
            }
        )

    return buf.getvalue()


def render_report(report: SupportAnalyticsReport, fmt: str) -> str:
    fmt_lower = fmt.lower()
    if fmt_lower == "markdown":
        return render_markdown(report)
    if fmt_lower == "json":
        return render_json(report)
    if fmt_lower == "csv":
        return render_csv(report)
    raise ValueError(f"Unsupported output format '{fmt}'. Choose markdown, json, or csv.")


def positive_float(raw: str) -> float:
    try:
        val = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected positive number, got '{raw}'.") from exc
    if val <= 0:
        raise argparse.ArgumentTypeError(f"Value must be > 0 (got {val}).")
    return val


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sla_calc.py",
        description=(
            "Support Ticket SLA, First-Response Time (FRT p50/p95), "
            "MTTR & CSAT Analytics CLI (100% Python stdlib)."
        ),
    )
    parser.add_argument(
        "csv_file",
        help="Path to support tickets CSV file (use '-' to read from stdin).",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("markdown", "json", "csv"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Optional output file path (defaults to stdout).",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Optional ISO timestamp to evaluate open/unanswered tickets against SLA.",
    )
    parser.add_argument(
        "--frt-sla-urgent",
        type=positive_float,
        default=DEFAULT_FRT_SLA_MINUTES["urgent"],
        help="Urgent FRT SLA target in minutes (default: 15).",
    )
    parser.add_argument(
        "--frt-sla-high",
        type=positive_float,
        default=DEFAULT_FRT_SLA_MINUTES["high"],
        help="High FRT SLA target in minutes (default: 60).",
    )
    parser.add_argument(
        "--frt-sla-normal",
        type=positive_float,
        default=DEFAULT_FRT_SLA_MINUTES["normal"],
        help="Normal FRT SLA target in minutes (default: 240).",
    )
    parser.add_argument(
        "--res-sla-urgent",
        type=positive_float,
        default=DEFAULT_RESOLUTION_SLA_MINUTES["urgent"],
        help="Urgent Resolution SLA target in minutes (default: 240).",
    )
    parser.add_argument(
        "--res-sla-high",
        type=positive_float,
        default=DEFAULT_RESOLUTION_SLA_MINUTES["high"],
        help="High Resolution SLA target in minutes (default: 480).",
    )
    parser.add_argument(
        "--res-sla-normal",
        type=positive_float,
        default=DEFAULT_RESOLUTION_SLA_MINUTES["normal"],
        help="Normal Resolution SLA target in minutes (default: 1440).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.csv_file == "-":
            records = parse_tickets_stream(sys.stdin)
        else:
            csv_path = Path(args.csv_file)
            if not csv_path.is_file():
                raise FileNotFoundError(f"Input CSV not found: {csv_path}")
            records = load_tickets_csv(csv_path)

        as_of_dt: Optional[datetime] = None
        if args.as_of:
            as_of_dt = parse_timestamp(args.as_of, "--as-of", 0)

        targets = SlaTargets(
            frt_minutes={
                "urgent": args.frt_sla_urgent,
                "high": args.frt_sla_high,
                "normal": args.frt_sla_normal,
            },
            resolution_minutes={
                "urgent": args.res_sla_urgent,
                "high": args.res_sla_high,
                "normal": args.res_sla_normal,
            },
        )
        report = analyze_tickets(records, sla_targets=targets, as_of=as_of_dt)
        rendered = render_report(report, args.format)

        if args.output:
            out_path = Path(args.output)
            out_path.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
            if not rendered.endswith("\n"):
                sys.stdout.write("\n")
        return 0
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
