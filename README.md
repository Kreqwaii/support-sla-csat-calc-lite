# support-sla-csat-calc-lite

**Support Ticket SLA, First-Response Time & CSAT Analytics CLI (Lite)** — 100% Python 3.9+ standard-library CLI utility for support leads, SaaS founders, and helpdesk operators.

Утилита на чистом Python 3.9+ (без `pip install` и сторонних библиотек) для аналитики службы поддержки: читает CSV-выгрузку тикетов (`ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score`), считает **FRT p50 / p95**, **Mean Time to Resolution (MTTR)**, **SLA Breach Rate** по приоритетам (`urgent` / `high` / `normal`), распределение оценок **CSAT 1–5** и **Net Satisfaction Score (NSS)** с экспортом в **Markdown, JSON или CSV**.

---

## Features / Возможности

- **Zero dependencies** — 100% Python 3.9+ `stdlib` (`csv`, `json`, `argparse`, `datetime`, `dataclasses`, `math`).
- **First-Response Time (FRT) Percentiles** — computes `p50` (median), `p95`, and mean FRT in minutes using linear interpolation.
- **Mean Time to Resolution (MTTR)** — calculates average, `p50`, and `p95` ticket resolution times across closed tickets.
- **SLA Breach Rate by Priority (`urgent` / `high` / `normal`)** — tracks FRT SLA breaches, Resolution SLA breaches, and overall SLA breach rates with customizable CLI targets.
- **CSAT 1–5 Distribution & Net Satisfaction Score (NSS)** — computes per-score counts & shares (`1` to `5`), survey response rate, mean CSAT, Top-2-Box % (`4–5`), Dissatisfied % (`1–2`), and `NSS = % Satisfied (4–5) - % Dissatisfied (1–2)`.
- **Category Breakdown & Flagged Action List** — surfaces categories with elevated breach rates or low CSAT and lists every SLA-breached or CSAT `<= 2` ticket.
- **Open Backlog Check (`--as-of`)** — optionally evaluates still-open / unanswered tickets against SLA targets as of a specific ISO timestamp.
- **Flexible Exports** — `--format markdown` (default), `--format json`, or `--format csv`, plus `--output <file>` and `-` stdin input.

---

## CSV Input Schema

Required header columns:

```csv
ticket_id,category,priority,created_at,first_response_at,closed_at,csat_score
```

- `ticket_id` — unique ticket identifier (string)
- `category` — ticket category (`billing`, `technical`, `account`, `bug`, `onboarding`, etc.; blank defaults to `uncategorized`)
- `priority` — one of `urgent`, `high`, `normal`
- `created_at` — ISO 8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`, offset-aware, or `YYYY-MM-DD`)
- `first_response_at` — optional ISO 8601 timestamp (leave blank for unanswered tickets)
- `closed_at` — optional ISO 8601 timestamp (leave blank for still-open tickets)
- `csat_score` — optional integer `1`–`5` (leave blank if not yet rated)

See [`sample_tickets.csv`](sample_tickets.csv) for a complete 20-ticket sample dataset.

### Default SLA Targets

| Priority | FRT SLA Target | Resolution SLA Target |
| :--- | ---: | ---: |
| `urgent` | 15 min | 240 min (4h) |
| `high` | 60 min (1h) | 480 min (8h) |
| `normal` | 240 min (4h) | 1440 min (24h) |

All targets can be overridden via `--frt-sla-{urgent,high,normal}` and `--res-sla-{urgent,high,normal}`.

---

## Quick Start / Быстрый старт

Run against the included sample dataset (Markdown output):

```bash
python sla_calc.py sample_tickets.csv
```

JSON output with custom urgent SLA thresholds and `--as-of` open backlog evaluation:

```bash
python sla_calc.py sample_tickets.csv --frt-sla-urgent 10 --res-sla-urgent 180 --as-of 2026-11-19T12:00:00Z --format json
```

CSV export to a file:

```bash
python sla_calc.py sample_tickets.csv --format csv --output sla_report.csv
```

Read from `stdin`:

```bash
cat sample_tickets.csv | python sla_calc.py - --format markdown
```

Run unit tests:

```bash
py -B -m unittest
```

---

## Example Output (Markdown)

```markdown
# Support Ticket SLA, FRT & CSAT Analytics Report

- **Total Tickets:** 20 (Responded: 19, Unanswered: 1, Closed: 17, Open: 3)

## Core Response, Resolution & CSAT Summary

| Metric | Value |
| :--- | ---: |
| First-Response Time (FRT) Mean | 88.00m (1h 28m) |
| **FRT p50 (Median)** | **60.00m (1h 0m)** |
| **FRT p95** | **300.00m (5h 0m)** |
| **Mean Time to Resolution (MTTR)** | **529.41m (8h 49m)** |
| Resolution p50 / p95 | 360.00m (6h 0m) / 1296.00m (21h 36m) |
| FRT SLA Breach Rate | 31.58% (6/19) |
| Resolution SLA Breach Rate | 23.53% (4/17) |
| **Overall SLA Breach Rate** | **36.84% (7/19)** |
| CSAT Survey Response Rate | 80.00% (16/20) |
| Mean CSAT (1-5) | 3.50 |
| CSAT Top-2-Box (4-5) | 56.25% |
| **Net Satisfaction Score (NSS)** | **31.25** |
```

---

## Need a Full Turnkey Telegram Support Ticket Desk & Knowledge Base FAQ Bot with Auto-CSAT & SLA Tracking?

This Lite CLI analyzes exported ticket CSV snapshots offline. If you need a **turnkey 24/7 Telegram Support Ticket Desk & Knowledge Base FAQ Bot** with forum-topic / agent routing, instant FAQ self-service deflection, automated post-resolution 1–5 CSAT surveys, and live SLA breach alerts:

- **Pro Full Turnkey Solution on FunPay:** https://funpay.com/lots/offer?id=82601748

---

## License

MIT License — see [LICENSE](LICENSE).
