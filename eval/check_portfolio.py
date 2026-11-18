"""Reconcile every portfolio row to independently queried snapshot records."""

from argparse import ArgumentParser
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import re

from client_intel.models import AS_OF
from client_intel.planning import open_snapshot
from eval.check_snapshot import read_json


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    bundle = read_json(args.run / "evidence.json")
    assert bundle["scope"] == "portfolio"
    rows = {row["client_id"]: row for row in bundle["rows"]}
    with closing(open_snapshot(args.data_dir)) as db:
        accounts = {row[0] for row in db.execute("SELECT DISTINCT client_id FROM clients")}
        assert set(rows) == accounts and len(rows) == len(bundle["rows"])
        for identifier, row in rows.items():
            terms = db.execute("""
                SELECT DISTINCT contract_id, end_date, notice_period_days, arr_eur FROM contracts
                WHERE client_id=? AND status='active' AND start_date<=? AND end_date>=?
                  AND signed_date<>'' AND signed_date<=?
            """, (identifier, str(AS_OF), str(AS_OF), str(AS_OF))).fetchall()
            open_p1 = db.execute("""
                SELECT COUNT(DISTINCT ticket_id) FROM support_tickets
                WHERE client_id=? AND priority='P1' AND opened_date<=? AND status='open' AND closed_date=''
            """, (identifier, str(AS_OF))).fetchone()[0]
            assert row["open_p1_tickets"] == open_p1
            if len(terms) == 1:
                _, end, notice_days, arr = terms[0]
                expected = date.fromisoformat(end) - timedelta(days=int(notice_days))
                assert row["signed_arr_eur"] == int(arr) and row["notice_date"] == str(expected)
            else:
                assert row["signed_arr_eur"] is None and row["notice_date"] is None
    markdown = (args.run / "portfolio.md").read_text(encoding="utf-8")
    cited = re.findall(r"\| .*? \((C\d+)\) \| .*? \[\^p(\d+)\] \|", markdown)
    assert [item[0] for item in cited] == list(rows)
    assert [int(item[1]) for item in cited] == list(range(1, len(rows) + 1))
    assert len(re.findall(r"^\[\^p\d+\]:", markdown, re.M)) == len(rows)
    print(f"{len(rows)} portfolio rows matched signed ARR, notice dates, open P1 counts and citations.")


if __name__ == "__main__":
    main()
