"""
Display-layer Chinese mappings for knowledge-graph visualization.

Does not modify GraphML, LightRAG storage, or entity IDs.
"""

from __future__ import annotations

# Common industrial pump entities (English key -> Chinese label).
# Keys are matched case-insensitively against entity display names / IDs.
ENTITY_ZH: dict[str, str] = {
    "water pump": "水泵",
    "pump": "泵",
    "centrifugal pump": "离心泵",
    "mechanical seal": "机械密封",
    "seal": "密封",
    "impeller": "叶轮",
    "bearing": "轴承",
    "shaft": "轴",
    "pump casing": "泵壳",
    "casing": "泵壳",
    "volute": "蜗壳",
    "gland": "填料压盖",
    "packing": "填料",
    "coupling": "联轴器",
    "motor": "电机",
    "baseplate": "底座",
    "base plate": "底座",
    "suction": "吸入",
    "discharge": "排出",
    "cavitation": "气蚀",
    "lubrication": "润滑",
    "oil": "润滑油",
    "grease": "润滑脂",
    "temperature": "温度",
    "vibration": "振动",
    "alignment": "对中",
    "clearance": "间隙",
    "dial indicator": "千分表",
    "dial indicator method": "千分表法",
    "bearing housing": "轴承箱",
    "wear ring": "耐磨环",
    "o-ring": "O 型圈",
    "gasket": "垫片",
    "valve": "阀门",
    "pipe": "管道",
    "piping": "管路",
    "flush": "冲洗",
    "cooling": "冷却",
    "startup": "启动",
    "start-up": "启动",
    "shutdown": "停机",
    "maintenance": "维护",
    "installation": "安装",
    "troubleshooting": "故障排查",
}

# LightRAG entity_type values -> Chinese legend labels.
TYPE_ZH: dict[str, str] = {
    "artifact": "设备",
    "concept": "概念",
    "content": "内容",
    "location": "位置",
    "naturalobject": "自然对象",
    "organization": "组织",
    "method": "方法",
    "event": "事件",
    "person": "人员",
    "data": "数据",
    "other": "其他",
    "component": "部件",
    "symptom": "症状",
    "process": "过程",
    "unknown": "未知",
}


def map_entity_zh(name: str) -> str | None:
    """Return Chinese label for an English entity name, or None if unmapped."""
    key = (name or "").strip().casefold()
    if not key:
        return None
    return ENTITY_ZH.get(key)


def map_type_zh(entity_type: str) -> str:
    """Return Chinese type label; fall back to original when unmapped."""
    raw = (entity_type or "").strip()
    if not raw:
        return "未知"
    mapped = TYPE_ZH.get(raw.casefold())
    return mapped if mapped else raw


def bilingual_entity_label(english_name: str, *, multiline: bool = True) -> str:
    """Build bilingual display text: Chinese + (English) when mapped."""
    en = (english_name or "").strip()
    if not en:
        return ""
    zh = map_entity_zh(en)
    if not zh:
        return en
    if multiline:
        return f"{zh}\n({en})"
    return f"{zh} ({en})"
