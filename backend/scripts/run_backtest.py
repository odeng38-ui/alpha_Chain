import argparse
import json
from datetime import date
from pathlib import Path

from app.db.session import SessionLocal
from app.services.backtest_service import BacktestService


def main():
    parser = argparse.ArgumentParser(description="Run a reproducible Alpha Score backtest")
    parser.add_argument("--name", default="alpha-score-backtest")
    parser.add_argument("--score-version", default="v1.0")
    parser.add_argument("--horizon", default="20d")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        row = BacktestService().run(
            db, args.name, args.score_version, args.horizon, args.start, args.end,
        )
        result = {"run_id": row.id, "status": row.status,
                  "dataset_hash": row.dataset_hash, "report": row.report}
        rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db.close()


if __name__ == "__main__":
    main()
