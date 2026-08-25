"""Convert a reviewed industrial-pump golden set into the evaluator JSONL contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(source: Path, output: Path) -> int:
    """Write evaluator-compatible JSONL and return the number of converted cases."""

    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            case_id = str(item["question_id"])
            question = str(item["question"])
            expects_evidence = bool(item["answerable"])
            evidence = item["gold_evidence"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"第 {line_number} 行不符合工业泵黄金集格式") from error
        if not case_id or not question or case_id in seen_ids or not isinstance(evidence, list):
            raise ValueError(f"第 {line_number} 行的题目 ID、问题或证据无效")
        if expects_evidence != bool(evidence):
            raise ValueError(f"第 {line_number} 行的 answerable 与 gold_evidence 不一致")
        rows.append(
            {
                "id": case_id,
                "question": question,
                "expects_evidence": expects_evidence,
                "expected_citations": evidence,
            }
        )
        seen_ids.add(case_id)

    if not rows:
        raise ValueError("黄金集不包含有效题目")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="转换工业泵黄金集为评测 JSONL")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"converted_cases={convert(args.source, args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
