# Admin Guide

## Users and roles
```bash
python scripts/dmis.py create-user admin@co.com "Admin" --role admin
python scripts/dmis.py create-user pm@co.com "Ni Zheng" --role product_manager --employee "Ni Zheng"
python scripts/dmis.py create-user sourcing@co.com "Sourcing" --role supplier_manager
```
| Role | Can |
|---|---|
| admin | everything, audit log, user admin |
| manager | all markets, suppliers, people; **approve / reject projects** |
| product_manager | own categories' markets only; create and move projects, recommend for approval |
| supplier_manager | manage suppliers; read markets, projects, people |
| analyst | read markets, upload data, create projects |
| viewer | read only |
| customer | Shopping Mode only |

Enforcement: `DIP_AUTH=on` (default on with PostgreSQL). Tokens are shown once and stored hashed.
Employee profile (department, responsibilities, e-mail): `POST /api/v2/employees/{id}/profile`.
Assign categories: ownership file (`bootstrap`) or `POST /api/v2/ownership`.

## Daily operations
- **Drop folder:** put each new export in `data/inbox/<market>/` (e.g. `data/inbox/micromotor/micromotor_2026-10.xlsx`).
- **Scheduler:** `python scripts/dmis.py schedule` (every 60 min; `--once` for one tick). Each tick processes new inbox
  files (a file is processed once), polls configured connectors, and after `summary_hour` sends each owner's daily
  summary once a day. Windows: run it from Task Scheduler; Linux/macOS: `cron` or a systemd service.
- **Summaries now:** `python scripts/dmis.py daily-summary` (print) or `--send`.
- **E-mail:** set `DIP_SMTP_HOST`, `DIP_SMTP_PORT` (587), `DIP_SMTP_USER`, `DIP_SMTP_PASSWORD`, `DIP_SMTP_FROM` and each
  employee's e-mail. Without SMTP, everything is delivered as internal messages (Inbox page).
- **Thresholds:** `config/platform/operations.yaml` (change alerts, scheduler, summary window, project stages,
  approval gate, outcome checkpoints). Changing config changes the input fingerprint, so the next upload recomputes.

## Audit
Every state-changing API request and key action (project approvals, stage changes, tracking, comments, scheduler runs)
is in the audit log: Audit Log page or `GET /api/v2/audit?action=project&user=...` (admin).

## Database upgrades
New columns are added automatically at start-up (`business.migrate`, additive and idempotent). Nothing is dropped.

## Organizations, plans and usage (P3b)
- The admins of the **default organization** are platform operators. On the Organization page, or through the API, they:
  - create organizations: `POST /api/v2/orgs {name, plan}` or `python scripts/dmis.py create-org "Acme Dental" --plan starter`
  - change a plan or suspend: `POST /api/v2/orgs/{id}/plan {plan, status}`
  - add users to an organization: `python scripts/dmis.py create-user ana@acme.com Ana --role analyst --org <org_id>`, or `POST /api/v2/auth/users {…, org_id}`
- Plans, limits, features and the token lifetime are in `config/platform/plans.yaml`. Going over a limit returns HTTP 402 with the reason.
- Each organization sees only its own markets, datasets, suppliers, graph and users. A market name belongs to the first organization that uploads it.
- Single-company installs never need an organization; everything stays in the default one.

## Tokens
- Tokens expire after `token_ttl_days` (default 90). Users create and revoke their own on the Organization page (`GET/POST /api/v2/auth/tokens`, `POST /api/v2/auth/tokens/{id}/revoke`).
- An admin can revoke any token.
- Expired or revoked tokens get 401.

## Suppliers
- Record cooperation on a supplier's page: inquiries, quotes, samples, orders, audits and issues.
- Quotes set the supplier's price level against the market's median quote.
- The ranking (Suppliers page, `GET /api/v2/sourcing/ranking?market=`) combines fit, cooperation rating, price, lead time and reliability. The weights are in `config/platform/sourcing.yaml`.

## Queue and workers (server)
- With `DIP_JOB_MODE=queue`, uploads wait in the queue until `python scripts/dmis.py worker` runs them. Run one or more workers; the production compose runs 2.
- `python scripts/dmis.py worker --once` processes a single job, which is useful for checking.
- Jobs whose worker died are re-queued after 6 h, at most 3 attempts.

## Backups
```bash
python scripts/dmis.py backup --out backups                      # daily, e.g. from cron
python scripts/dmis.py restore backups/dmis-backup-<time>.tar.gz [--force]
```
- An archive holds `data/platform` and `data/lake`, a sha256 manifest, and a `pg_dump` when PostgreSQL is used.
- Neo4j and Qdrant: use their native dumps, or re-process the datasets.
- `data/raw/` is source evidence; keep it separately.
- Details are in `DEPLOYMENT_GUIDE.md`.
