"""Build the frozen Phase 10A multi-evidence golden set from real child chunks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from industrial_rag.evidence_policy import _tokens

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLD_GOLDEN = PROJECT_ROOT / "data/evaluation/industrial_pump_golden_set_50.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "evaluation/phase10"
GOLDEN_PATH = OUTPUT_DIR / "expanded_golden_set.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "golden_set_manifest.json"
CHILD_PATHS = (
    "evaluation/experiments/parser_backend/fixed_model/P1_mineru/"
    "2196-ANSI-Manual-Chinese.pdf/child_chunks.jsonl",
    "evaluation/experiments/parser_backend/fixed_model/P1_mineru/"
    "t1739cn.pdf/child_chunks.jsonl",
)
PDF_PATHS = (
    "data/manuals/2196-ANSI-Manual-Chinese.pdf",
    "data/manuals/t1739cn.pdf",
)

DEVELOPMENT_IDS = tuple(
    [f"S{index:03d}" for index in range(1, 21)]
    + [f"D{index:03d}" for index in range(1, 17)]
)
VALIDATION_IDS = tuple(
    [f"D{index:03d}" for index in range(17, 21)]
    + [f"C{index:03d}" for index in range(1, 9)]
    + ["N001", "N002", "A001", "A002"]
)
HOLDOUT_IDS = tuple([f"A{index:03d}" for index in range(3, 13)] + ["N003", "N004"])

CROSS_PAGE_IDS = {"S011", "S017", "D015", "D019", "D020", "A012"}
MULTI_EVIDENCE_IDS = {f"C{index:03d}" for index in range(1, 9)}
TYPE_BY_ID = {
    "S001": "maintenance_interval",
    "S002": "procedure",
    "S003": "procedure",
    "S004": "parameter",
    "S005": "parameter",
    "S006": "condition_limit",
    "S007": "table",
    "S008": "maintenance_interval",
    "S009": "maintenance_interval",
    "S010": "safety_warning",
    "S012": "procedure",
    "S013": "table",
    "S014": "procedure",
    "S015": "troubleshooting",
    "S016": "troubleshooting",
    "S018": "condition_limit",
    "S019": "component_description",
    "S020": "procedure",
    "D001": "component_description",
    "D002": "safety_warning",
    "D003": "terminology",
    "D004": "condition_limit",
    "D005": "safety_warning",
    "D006": "procedure",
    "D007": "safety_warning",
    "D008": "maintenance_interval",
    "D009": "unit_expression",
    "D010": "parameter",
    "D011": "unit_expression",
    "D012": "procedure",
    "D013": "condition_limit",
    "D014": "condition_limit",
    "D016": "parameter",
    "D017": "table",
    "D018": "procedure",
    "A001": "condition_limit",
    "A002": "procedure",
    "A003": "terminology",
    "A004": "procedure",
    "A005": "condition_limit",
    "A006": "parameter",
    "A007": "procedure",
    "A008": "maintenance_interval",
    "A009": "maintenance_interval",
    "A010": "table",
    "A011": "safety_warning",
}

ADDITIONAL_POSITIVES = (
    ("A001", "SUMMIT 手册规定的最高轴承工作温度和填料函强制水冲注温度条件分别是什么？", "2196-ANSI-Manual-Chinese.pdf", (14,)),
    ("A002", "SUMMIT 轴承架添加或排出润滑油时，油位应保持在观察窗什么位置？", "2196-ANSI-Manual-Chinese.pdf", (15,)),
    ("A003", "DESMI 产品安全标签在启动前要求确认哪些英文和中文事项？", "t1739cn.pdf", (8,)),
    ("A004", "DESMI 泵组室外存放和接口封盖分别有什么要求？", "t1739cn.pdf", (15,)),
    ("A005", "DESMI 手册对环境温度超过 40°C 或海拔超过 1000 m 时有什么提示？", "t1739cn.pdf", (17,)),
    ("A006", "DESMI 最小进水压力表达式包含哪些压力和损失项？", "t1739cn.pdf", (18,)),
    ("A007", "DESMI 为避免杂质进入泵内建议安装哪些过滤和监测装置？", "t1739cn.pdf", (24,)),
    ("A008", "DESMI 泵不运行时，为避免轴封和轴承长期静置损坏，应多久转动泵轴？", "t1739cn.pdf", (31,)),
    ("A009", "DESMI 泵长期停机但保持安装时，建议以什么频率启动、每次运行多久？", "t1739cn.pdf", (35,)),
    ("A010", "DESMI 重载结构前后轴承的加脂量和重润滑周期分别是多少？", "t1739cn.pdf", (45,)),
    ("A011", "DESMI 泵处理过有毒、爆炸或高温液体后，排水清洗和转移前有哪些要求？", "t1739cn.pdf", (47,)),
    ("A012", "DESMI 扭矩表中 M24 的 12.9 级螺栓螺母与 A2-50 级螺栓螺母锁紧扭矩范围分别是多少？", "t1739cn.pdf", (56, 57)),
)

NEGATIVES = (
    ("N001", "这两份泵手册中，Wi-Fi 配网和无线网络密码应该如何设置？", "两份手册均未覆盖 Wi-Fi 配网或无线密码。", "negative"),
    ("N002", "两份手册指定必须购买哪个品牌、哪个型号的变频器？", "手册仅讨论变频器相关安装或运行条件，未强制指定采购品牌型号。", "confusing_device"),
    ("N003", "这两份泵手册如何注册远程云平台账号并获取手机验证码？", "两份手册均未覆盖云平台账号或手机验证码。", "negative"),
    ("N004", "两份手册要求选用哪个品牌和型号的 PLC 控制器？", "两份手册均未指定 PLC 品牌和型号。", "confusing_device"),
)

QUESTION_OVERRIDES = {
    "D007": "运输 DESMI 泵组时，应怎样检查运输损坏、固定泵组并布置吊带？",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_chunks() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for relative_path in CHILD_PATHS:
        for line in (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            rows.append(row)
            by_id[row["chunk_id"]] = row
    return rows, by_id


def _best_chunk(
    chunks: list[dict[str, Any]], document_name: str, page: int, question: str
) -> dict[str, Any]:
    page_candidates = [
        row
        for row in chunks
        if row["document_name"] == document_name
        and row["page_start"] <= page <= row["page_end"]
    ]
    candidates = [
        row for row in page_candidates if row["content_type"] != "section_heading"
    ] or page_candidates
    if not candidates:
        raise ValueError(f"no evidence chunk for {document_name} page {page}")
    question_terms = _tokens(question)
    return max(
        candidates,
        key=lambda row: (
            len(question_terms & _tokens(row["content"])),
            -len(row["content"]),
            row["chunk_id"],
        ),
    )


def _excerpt(question: str, content: str, *, limit: int = 600) -> str:
    terms = sorted(_tokens(question), key=lambda value: (-len(value), value))
    positions = (
        content.casefold().find(term.casefold())
        for term in terms
    )
    center = next((position for position in positions if position >= 0), 0)
    start = max(0, center - limit // 3)
    end = min(len(content), start + limit)
    start = max(0, end - limit)
    return content[start:end].strip()


def _split_for(question_id: str) -> str:
    if question_id in DEVELOPMENT_IDS:
        return "development"
    if question_id in VALIDATION_IDS:
        return "validation"
    if question_id in HOLDOUT_IDS:
        return "holdout"
    raise ValueError(f"question has no frozen split: {question_id}")


def _question_type(question_id: str) -> str:
    if question_id in CROSS_PAGE_IDS:
        return "cross_page"
    if question_id in MULTI_EVIDENCE_IDS:
        return "multi_evidence"
    return TYPE_BY_ID[question_id]


def _positive_row(
    *,
    question_id: str,
    question: str,
    citations: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    selected: list[tuple[dict[str, Any], int]] = []
    seen: set[str] = set()
    for citation in citations:
        chunk = _best_chunk(
            chunks,
            citation["source_file"],
            int(citation["page_number"]),
            question,
        )
        if chunk["chunk_id"] not in seen:
            selected.append((chunk, int(citation["page_number"])))
            seen.add(chunk["chunk_id"])
    if question_id == "A012":
        forced = (
            "cchunk-pymupdf-v1-oot-5b8e825d8f7f-000-305096907bbe",
            "cchunk-pymupdf-v1-oot-5b8e825d8f7f-001-505a3713c38e",
        )
        by_id = {row["chunk_id"]: row for row in chunks}
        selected = [(by_id[forced[0]], 56), (by_id[forced[1]], 57)]
    expected_evidence = []
    expected_points = []
    for index, (chunk, page) in enumerate(selected, start=1):
        evidence_id = f"{question_id}-e{index}"
        evidence_text = _excerpt(question, chunk["content"])
        if question_id == "A012" and index == 2:
            anchor = chunk["content"].find("行9：M20")
            evidence_text = chunk["content"][anchor : anchor + 520].strip()
        expected_evidence.append(
            {
                "evidence_id": evidence_id,
                "document_name": chunk["document_name"],
                "page_number": page,
                "chunk_id": chunk["chunk_id"],
                "evidence_text": evidence_text,
                "role": "primary" if index == 1 else "supporting",
                "relevance_grade": 2 if index == 1 else 1,
            }
        )
        expected_points.append(
            {
                "point_id": f"{question_id}-p{index}",
                "text": evidence_text,
                "supported_by": [evidence_id],
            }
        )
    return {
        "question_id": question_id,
        "question": question,
        "answerable": True,
        "expected_evidence": expected_evidence,
        "expected_answer_points": expected_points,
        "question_type": _question_type(question_id),
        "difficulty": "hard" if len(expected_evidence) > 1 else "medium",
        "negative_reason": None,
        "split": _split_for(question_id),
    }


def build_rows() -> list[dict[str, Any]]:
    chunks, _ = _load_chunks()
    old_rows = [
        json.loads(line)
        for line in OLD_GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows: list[dict[str, Any]] = []
    negative_by_id = {item[0]: item for item in NEGATIVES}
    for old in old_rows:
        question_id = old["id"]
        if not old["expects_evidence"]:
            continue
        rows.append(
            _positive_row(
                question_id=question_id,
                question=QUESTION_OVERRIDES.get(question_id, old["question"]),
                citations=old["expected_citations"],
                chunks=chunks,
            )
        )
    for question_id, question, document_name, pages in ADDITIONAL_POSITIVES:
        rows.append(
            _positive_row(
                question_id=question_id,
                question=question,
                citations=[
                    {"source_file": document_name, "page_number": page} for page in pages
                ],
                chunks=chunks,
            )
        )
    for question_id in ("N001", "N002", "N003", "N004"):
        _, question, reason, question_type = negative_by_id[question_id]
        rows.append(
            {
                "question_id": question_id,
                "question": question,
                "answerable": False,
                "expected_evidence": [],
                "expected_answer_points": [],
                "question_type": question_type,
                "difficulty": "medium",
                "negative_reason": reason,
                "split": _split_for(question_id),
            }
        )
    order = {item: index for index, item in enumerate(
        (*DEVELOPMENT_IDS, *VALIDATION_IDS, *HOLDOUT_IDS)
    )}
    return sorted(rows, key=lambda row: order[row["question_id"]])


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "manifest_version": "phase10a-golden-manifest-v1",
        "annotation_policy_version": "phase10a-multi-evidence-v1",
        "dataset_path": "evaluation/phase10/expanded_golden_set.jsonl",
        "dataset_sha256": _sha256(GOLDEN_PATH),
        "record_count": len(rows),
        "positive_count": sum(row["answerable"] for row in rows),
        "negative_count": sum(not row["answerable"] for row in rows),
        "split_distribution": dict(Counter(row["split"] for row in rows)),
        "question_type_distribution": dict(
            sorted(Counter(row["question_type"] for row in rows).items())
        ),
        "source_pdfs": [
            {"path": path, "sha256": _sha256(PROJECT_ROOT / path)}
            for path in PDF_PATHS
        ],
        "child_chunk_artifacts": [
            {"path": path, "sha256": _sha256(PROJECT_ROOT / path)}
            for path in CHILD_PATHS
        ],
        "creation_commit": _git_head(),
        "holdout_not_used_for_tuning": True,
        "metric_policy": {
            "metrics": [
                "chunk_recall_at_k",
                "any_evidence_recall_at_k",
                "complete_evidence_recall_at_k",
                "document_recall_at_k",
                "page_recall_at_k",
                "mrr",
                "graded_ndcg_at_10",
                "false_rejection_rate",
                "negative_rejection_rate",
                "unsupported_answer_rate",
                "question_level_citation_accuracy",
            ],
            "retrieval_denominator": "answerable_positive_questions_only",
            "negative_questions_in_retrieval_denominator": False,
            "rate_shape": ["numerator", "denominator", "value"],
            "empty_denominator_value": None,
            "claim_level_citation_accuracy": {
                "available": False,
                "value": None,
                "reason": "claim_to_evidence_ground_truth_not_annotated",
            },
        },
    }


def main() -> int:
    rows = build_rows()
    if len(rows) != 64:
        raise ValueError(f"expected 64 rows, got {len(rows)}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(rows), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("records=64 positives=60 negatives=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
