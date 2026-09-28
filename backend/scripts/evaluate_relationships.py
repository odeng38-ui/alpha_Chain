import argparse
import json
from pathlib import Path

from app.services.relationship_service import RelationshipExtractionService


def load_edges(path: Path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {(int(row["source_id"]), int(row["target_id"]), row["type"]) for row in rows}


def main():
    parser = argparse.ArgumentParser(description="Evaluate relationship extraction against a gold set")
    parser.add_argument("predicted", type=Path)
    parser.add_argument("gold", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = RelationshipExtractionService.evaluate(load_edges(args.predicted), load_edges(args.gold))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
