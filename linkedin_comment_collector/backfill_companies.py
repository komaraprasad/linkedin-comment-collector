"""
Re-verifies company_website / company_linkedin_url for every stored comment using company_enrichment.

Usage:
    python backfill_companies.py              # dry run: writes a CSV report, changes nothing
    python backfill_companies.py --apply      # backs up current values to JSON, then updates the database

Uses DATABASE_URL (Postgres/Neon) when set, otherwise the local SQLite file.
"""
import os
import sys
import csv
import json
import datetime

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
load_dotenv(os.path.join(os.path.dirname(HERE), ".env.local"))
load_dotenv(os.path.join(HERE, ".env.local"))

from db_manager import get_connection, DB_FILE, is_postgres  # noqa: E402
from company_enrichment import resolve_many, LI_STATS  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.environ.get("BACKFILL_OUT_DIR", HERE)

    conn = get_connection(DB_FILE)
    try:
        c = conn.cursor()
        c.execute("""
            SELECT id, author_name, email, author_headline, comment_text, company_website, company_linkedin_url
            FROM comments
        """)
        rows = [dict(r) for r in c.fetchall()]
    finally:
        conn.close()  # lookups take minutes; Neon drops idle connections, so reconnect for the write
    if True:
        print(f"Engine: {'PostgreSQL' if is_postgres() else 'SQLite'} | comments: {len(rows)}")

        # Stored values were produced by older guessing code, so they are not used as hints: every row is
        # re-derived from its email, headline and comment text only.
        inputs = [{**r, "company_website": None, "company_linkedin_url": None} for r in rows]
        results = resolve_many(inputs, max_workers=4, time_budget_s=3600)
        if LI_STATS["blocked"]:
            print(f"WARNING: {LI_STATS['blocked']} LinkedIn lookups stayed rate-limited; those links may be missing. Re-run later.")

        changes = []
        for r, (web, li) in zip(rows, results):
            if (r["company_website"] or None) != web or (r["company_linkedin_url"] or None) != li:
                changes.append({
                    "id": r["id"], "author_name": r["author_name"], "email": r["email"],
                    "headline": (r["author_headline"] or "")[:80],
                    "old_website": r["company_website"], "new_website": web,
                    "old_linkedin": r["company_linkedin_url"], "new_linkedin": li,
                })

        report = os.path.join(out_dir, f"backfill_report_{stamp}.csv")
        with open(report, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "author_name", "email", "headline", "old_website", "new_website", "old_linkedin", "new_linkedin"])
            w.writeheader()
            w.writerows(changes)

        filled_web = sum(1 for _, (web, _l) in zip(rows, results) if web)
        filled_li = sum(1 for _, (_w, li) in zip(rows, results) if li)
        print(f"Rows changing: {len(changes)} | verified websites: {filled_web} | verified LinkedIn pages: {filled_li}")
        print(f"Report: {report}")

        if not apply:
            print("Dry run only. Re-run with --apply to update the database.")
            return

        backup = os.path.join(out_dir, f"backfill_backup_{stamp}.json")
        with open(backup, "w", encoding="utf-8") as f:
            json.dump([{k: r[k] for k in ("id", "company_website", "company_linkedin_url")} for r in rows], f)
        print(f"Backup of previous values: {backup}")

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_connection(DB_FILE)
        c = conn.cursor()
        c.executemany(
            "UPDATE comments SET company_website = ?, company_linkedin_url = ?, updated_at = ? WHERE id = ?",
            [(ch["new_website"], ch["new_linkedin"], now, ch["id"]) for ch in changes],
        )
        conn.commit()
        conn.close()
        print(f"Updated {len(changes)} rows.")


if __name__ == "__main__":
    main()
