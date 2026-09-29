# Pilot runbook (draft)

This draft is written before the pilot server exists. Fill in the bracketed items once it does. The deployment details are in `docs/DEPLOYMENT_GUIDE.md`.

## Server — [host, domain, owner]

| Task | Command |
|---|---|
| Start / update | `docker compose -f infra/docker-compose.prod.yml --env-file infra/prod.env up -d --build` |
| Stop | `docker compose -f infra/docker-compose.prod.yml down` (volumes are kept) |
| Logs | `docker compose -f infra/docker-compose.prod.yml logs -f api worker scheduler` |
| Add a user | `docker compose ... exec api python scripts/dmis.py create-user EMAIL "NAME" --role ROLE [--employee "NAME"]` |
| Assign categories | Organization / ownership (`POST /api/v2/ownership`) or the ownership CSV |
| New monthly export | Copy it to the `inbox` volume under `<market>/` (e.g. `micromotor/micromotor_2026-10.xlsx`); the scheduler processes it within the hour. Or upload it on Data Operations with its snapshot date. |
| Backup (nightly, cron) | `docker compose ... exec api python scripts/dmis.py backup --out /app/backups`, then copy `./backups` off the server |
| Restore | `python scripts/dmis.py restore ARCHIVE --force` into the `platform` and `lake` volumes; then `psql < postgres.sql` into an empty database |

**Restore drill:** not done yet. Record here the date, the archive used, the time it took and any problems. It must be done once before the pilot starts.

**Load check:** not done yet. Run it with the largest real export and 2 workers. Record the processing time, and the API p50/p95 response times for `/markets`, `/markets/{m}` and `/markets/{m}/products`.

## Accuracy labelling (Phase 1)

1. On **Accuracy labelling**, pick the market and draw a sample: 50 products, 20 excluded listings, seed 7.
2. For each product:
   - Tick listings that are **not this category** (for example, a water flosser in implants) or **not this product** (a different model or pack grouped in by mistake).
   - List any ASINs of the same product that were left out.
   - Judge the segment, the best-selling listing and the extracted attributes.
3. For each excluded listing, answer whether it belongs in the market. This measures what the system wrongly dropped.
4. Offline alternative: download the CSV sheet, fill it in, and import it.
   - Leave a cell blank for "not judged".
   - Write `none` for "no errors".
   - Use `yes` / `no` for the correct columns.
5. Regenerate `docs/pilot/ACCURACY_REPORT.md` (`python scripts/pilot.py accuracy --out ...`).
6. After a rule fix in `config/`, re-process the market and read **Current (after)**. Then draw a new seed as a holdout. Your own relevance labels override those listings on re-runs, so the same sample overstates relevance after a fix.

## Weekly pilot loop (Phase 6)

1. **Monday:**
   - Triage every new report on **Feedback & usage**: data_bug, rule_fix, ui_confusion, feature_request or not_a_bug.
   - Each data_bug or rule_fix gets a fix with a regression test. Record the commit in the resolution field.
2. Review usage: which pages each user opened, and which pages nobody uses.
3. Write `docs/pilot/WEEK_n.md` covering usage, feedback (triaged), fixes shipped and open issues.
4. Projects: two or three real product ideas go through the Product Pipeline with real approvals.

## Contacts

[pilot owner] · [server admin] · [data owner for SellerSprite exports]
