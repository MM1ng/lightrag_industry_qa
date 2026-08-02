"""Grounded Answer Lite: inline chunk markers, key claims, local repair, guard."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import PHASE5B_ROOT

INSUFFICIENT_EVIDENCE_ANSWER = "现有资料不足以回答该问题。"
MAX_CITATIONS_PER_SENTENCE = 2

_MARKER_PATTERN = re.compile(r"\[引用:([^\]]*)\]")
_PARAMETER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mm|cm|m|km|bar|MPa|kPa|psi|Pa|°C|℃|°F|rpm|RPM|Hz|kW|MW|W|V|A|mA|"
    r"L/min|l/min|m³/h|m3/h|kg|%|N·m|Nm|kgf/cm2|mg/L|ppm)"
    r"|温度|压力|流量|功率|转速|电压|电流|频率|扬程|型号|规格|尺寸|扭矩|间隙|公差|寿命|周期|阈值|限值|液位"
)
_PROCEDURE_PATTERN = re.compile(
    r"^\s*\d+[.、)]|步骤|按下|打开|关闭|启动|停止|安装|拆卸|拧紧|松开|调整|检查|清洗|加注|排空|"
    r"确保|然后|首先|其次|最后|应进行|操作"
)
_SAFETY_PATTERN = re.compile(
    r"必须|禁止|严禁|警告|危险|联锁|旁路|隔离|泄压|高温|高压|人员防护|切勿|不得|请勿|防护"
)
_TROUBLESHOOTING_PATTERN = re.compile(
    r"故障|原因|处理措施|处理|措施|诊断|异常|导致|造成|更换|修复|检查并|(?:若|如|当).{0,8}(?:出现|发生|发现)"
)
_ASSERTION_PATTERN = re.compile(r"应|需|可|会|是|用于|保证|防止|避免|建议|必须|能够")
_TRANSITION_PATTERN = re.compile(
    r"^(?:因此|总之|综上|以上|所以|由此可见|需要注意的是|另外|此外|同时)[，,]"
)


def load_frozen() -> dict[str, Any]:
    return json.loads(
        (PHASE5B_ROOT / "config" / "frozen_common.json").read_text(encoding="utf-8")
    )


def load_prompt(name: str) -> str:
    return (PHASE5B_ROOT / "prompts" / name).read_text(encoding="utf-8")


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence split with trailing citation markers attached.

    Splits after sentence punctuation (and newlines); any leading ``[引用:...]``
    block of a segment is attached to the previous sentence so a marker placed
    after ``。`` always belongs to the sentence it follows.
    """
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("[引用:"):
            match = re.match(r"^((?:\[引用:[^\]]*\]\s*)+)(.*)$", part, flags=re.DOTALL)
            if match:
                marker_block = match.group(1).strip()
                rest = match.group(2).strip()
                if merged:
                    merged[-1] = f"{merged[-1]} {marker_block}"
                elif marker_block:
                    merged.append(marker_block)
                if rest:
                    merged.append(rest)
                continue
        merged.append(part)
    return merged


def detect_key_claim(sentence: str) -> tuple[bool, list[str]]:
    """Fixed-rule key claim detection (no gold labels, no categories)."""
    text = sentence.strip()
    if not text:
        return False, []
    types: list[str] = []
    if _PARAMETER_PATTERN.search(text):
        types.append("parameter")
    if _PROCEDURE_PATTERN.search(text):
        types.append("procedure")
    if _SAFETY_PATTERN.search(text):
        types.append("safety")
    if _TROUBLESHOOTING_PATTERN.search(text):
        types.append("troubleshooting")
    if (
        not types
        and len(text) >= 8
        and not _TRANSITION_PATTERN.match(text)
        and _ASSERTION_PATTERN.search(text)
    ):
        types.append("fact")
    return bool(types), types


def parse_markers(sentence: str) -> tuple[list[str], list[str]]:
    """Return (valid marker chunks, malformed marker substrings)."""
    chunks: list[str] = []
    malformed: list[str] = []
    for match in _MARKER_PATTERN.finditer(sentence):
        inner = match.group(1).strip()
        if not inner:
            malformed.append(match.group(0))
            continue
        for part in re.split(r"[,\u3001，]", inner):
            part = part.strip()
            if part:
                chunks.append(part)
    return chunks, malformed


def remove_markers(sentence: str) -> str:
    return _MARKER_PATTERN.sub("", sentence).strip()


def build_whitelist_text(registry: dict[str, dict[str, Any]]) -> str:
    return "\n".join(
        f"- E{index + 1} (chunk_id: {chunk_id})"
        for index, chunk_id in enumerate(sorted(registry))
    )


def build_alias_map(registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {
        f"E{index + 1}": chunk_id
        for index, chunk_id in enumerate(sorted(registry))
    }


def build_evidence_block(registry: dict[str, dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, chunk_id in enumerate(sorted(registry)):
        info = registry[chunk_id]
        blocks.append(
            "[证据开始]\n"
            f"引用别名: E{index + 1}\n"
            f"chunk_id: {chunk_id}\n"
            f"来源: {info['document']}\n"
            f"页码: {info['page']}\n"
            "正文:\n"
            f"{info['text']}\n"
            "[证据结束]"
        )
    return "\n\n".join(blocks)


def process_sentences(
    answer: str,
    *,
    whitelist: set[str],
    registry: dict[str, dict[str, Any]],
    alias_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Marker parsing, whitelist validation, key-claim detection per sentence."""
    sentences = split_sentences(answer)
    sentence_info: list[dict[str, Any]] = []
    total_markers = 0
    valid_markers = 0
    invalid_chunk_markers = 0
    malformed_markers = 0
    key_claims = 0
    covered_key_claims = 0
    coverage_by_type: dict[str, list[int]] = {}
    for index, sentence in enumerate(sentences):
        markers, malformed = parse_markers(sentence)
        total_markers += len(markers) + len(malformed)
        malformed_markers += len(malformed)
        resolved: list[str] = []
        for marker in markers:
            resolved.append(alias_map.get(marker, marker) if alias_map else marker)
        unique: list[str] = []
        seen: set[str] = set()
        for chunk_id in resolved:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            unique.append(chunk_id)
        if len(unique) > MAX_CITATIONS_PER_SENTENCE:
            unique = unique[:MAX_CITATIONS_PER_SENTENCE]
        valid = [c for c in unique if c in whitelist]
        invalid = [c for c in unique if c not in whitelist]
        valid_markers += len(valid)
        invalid_chunk_markers += len(invalid)
        citations = [
            {
                "chunk_id": chunk_id,
                "document_name": registry[chunk_id]["document"],
                "page": registry[chunk_id]["page"],
            }
            for chunk_id in valid
        ]
        clean_sentence = remove_markers(sentence)
        is_key, types = detect_key_claim(clean_sentence)
        if is_key:
            key_claims += 1
            if citations:
                covered_key_claims += 1
            for claim_type in types:
                coverage_by_type.setdefault(claim_type, [0, 0])
                coverage_by_type[claim_type][1] += 1
                if citations:
                    coverage_by_type[claim_type][0] += 1
        if is_key and not citations:
            validation_status = "uncited"
        elif invalid or malformed:
            validation_status = "invalid_marker"
        else:
            validation_status = "ok"
        sentence_info.append(
            {
                "sentence_index": index,
                "sentence": sentence,
                "clean_sentence": clean_sentence,
                "key_claim": is_key,
                "detected_types": types,
                "citation_marker": markers,
                "citations": citations,
                "valid_citation_count": len(valid),
                "invalid_chunk_ids": invalid,
                "malformed_markers": malformed,
                "validation_status": validation_status,
            }
        )
    return {
        "sentences": sentence_info,
        "marker_stats": {
            "total_markers": total_markers,
            "valid_markers": valid_markers,
            "invalid_chunk_markers": invalid_chunk_markers,
            "malformed_markers": malformed_markers,
        },
        "coverage": {
            "key_claims": key_claims,
            "covered_key_claims": covered_key_claims,
            "by_type": coverage_by_type,
        },
        "clean_answer": "\n".join(info["clean_sentence"] for info in sentence_info),
    }


def apply_claim_guard(processed: dict[str, Any]) -> dict[str, Any]:
    """GL3: prune un-cited key claims per sentence; refuse only if all pruned."""
    kept: list[dict[str, Any]] = []
    removed = 0
    removed_sentences: list[str] = []
    for info in processed["sentences"]:
        if info["key_claim"] and info["valid_citation_count"] == 0:
            removed += 1
            removed_sentences.append(info["clean_sentence"])
            continue
        kept.append(info)
    answer = "\n".join(info["clean_sentence"] for info in kept)
    status = "insufficient_evidence" if not kept else "answered"
    if status == "insufficient_evidence":
        answer = INSUFFICIENT_EVIDENCE_ANSWER
    return {
        "kept_sentences": kept,
        "removed_claim_count": removed,
        "removed_sentences": removed_sentences,
        "answer": answer,
        "status": status,
        "empty_after_pruning": status == "insufficient_evidence",
    }


def validate_repair_output(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse and validate the citation-only repair JSON mapping."""
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as error:
        return None, [f"repair JSON invalid: {error}"]
    if not isinstance(parsed, dict):
        return None, ["repair output is not an object"]
    unexpected = set(parsed) - {"sentence_citations"}
    if unexpected:
        return None, [f"repair output contains unexpected keys: {sorted(unexpected)}"]
    mappings = parsed.get("sentence_citations")
    if not isinstance(mappings, list):
        return None, ["sentence_citations must be a list"]
    errors: list[str] = []
    cleaned: list[dict[str, Any]] = []
    for item in mappings:
        if not isinstance(item, dict) or "sentence_index" not in item or "chunk_ids" not in item:
            errors.append("mapping item missing sentence_index/chunk_ids")
            continue
        index = item["sentence_index"]
        chunk_ids = item["chunk_ids"]
        if not isinstance(index, int) or index < 0:
            errors.append(f"invalid sentence_index {index!r}")
            continue
        if not isinstance(chunk_ids, list) or not all(isinstance(c, str) for c in chunk_ids):
            errors.append(f"invalid chunk_ids for sentence {index}")
            continue
        unique = list(dict.fromkeys(chunk_ids))
        if len(unique) > MAX_CITATIONS_PER_SENTENCE:
            errors.append(f"sentence {index} exceeds {MAX_CITATIONS_PER_SENTENCE} citations")
            unique = unique[:MAX_CITATIONS_PER_SENTENCE]
        cleaned.append({"sentence_index": index, "chunk_ids": unique})
    if errors:
        return None, errors
    return {"sentence_citations": cleaned}, []


def apply_repair_mapping(
    processed: dict[str, Any],
    mapping: dict[str, Any],
    *,
    whitelist: set[str],
    registry: dict[str, dict[str, Any]],
    alias_map: dict[str, str] | None = None,
    answer_text_hash_before: str,
) -> dict[str, Any]:
    """Attach repaired citations to sentences; answer text must stay identical."""
    by_index = {info["sentence_index"]: info for info in processed["sentences"]}
    errors: list[str] = []
    for item in mapping.get("sentence_citations", []):
        index = item["sentence_index"]
        info = by_index.get(index)
        if info is None:
            errors.append(f"repair referenced missing sentence_index {index}")
            continue
        resolved = [
            alias_map.get(c, c) if alias_map else c for c in item["chunk_ids"]
        ]
        chunk_ids = [c for c in resolved if c in whitelist]
        pool_out = [c for c in resolved if c not in whitelist]
        if pool_out:
            errors.append(f"sentence {index} pool-out chunk_ids {pool_out}")
        info["citations"] = [
            {
                "chunk_id": c,
                "document_name": registry[c]["document"],
                "page": registry[c]["page"],
            }
            for c in chunk_ids
        ]
        info["valid_citation_count"] = len(chunk_ids)
        info["validation_status"] = "ok" if chunk_ids else info["validation_status"]
        info["repair_mapping_applied"] = True
    # Recompute coverage after repair
    coverage = {"key_claims": 0, "covered_key_claims": 0, "by_type": {}}
    for info in processed["sentences"]:
        if not info["key_claim"]:
            continue
        coverage["key_claims"] += 1
        if info["valid_citation_count"]:
            coverage["covered_key_claims"] += 1
        for claim_type in info["detected_types"]:
            coverage["by_type"].setdefault(claim_type, [0, 0])
            coverage["by_type"][claim_type][1] += 1
            if info["valid_citation_count"]:
                coverage["by_type"][claim_type][0] += 1
    processed["coverage"] = coverage
    processed["repair_errors"] = errors
    processed["answer_text_hash_after"] = hashlib.sha256(
        processed["clean_answer"].encode("utf-8")
    ).hexdigest()
    processed["answer_text_hash_before"] = answer_text_hash_before
    processed["answer_text_unchanged"] = (
        processed["answer_text_hash_after"] == answer_text_hash_before
    )
    return processed


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
