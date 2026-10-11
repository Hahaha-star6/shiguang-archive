# -*- coding: utf-8 -*-
"""
敏感信息自动脱敏模块（M2）
功能：扫描 解析结果/ 目录下的解析文本，自动检测身份证号、手机号等敏感信息，
     打码后输出脱敏副本到 脱敏结果/ 目录，并生成检测报告 脱敏报告.json。

用法：python desensitize.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
SRC_DIR = BASE / "解析结果"
OUT_DIR = BASE / "脱敏结果"
REPORT_PATH = BASE / "脱敏报告.json"

# 身份证号：18位 = 前6位 + 8位 + 末3位数字加1位校验位（数字或X）
ID_PATTERN = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
# 手机号：1[3-9] 开头，共11位
PHONE_PATTERN = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")

RULES = [
    ("身份证号", ID_PATTERN, lambda m: f"{m.group(1)}********{m.group(2)}"),
    ("手机号", PHONE_PATTERN, lambda m: f"{m.group(1)}****{m.group(2)}"),
]


def desensitize(text: str):
    """对文本执行全部脱敏规则，返回 (脱敏后文本, 命中详情)"""
    hits = []
    for name, pattern, replacer in RULES:
        def _repl(m, _name=name, _replacer=replacer):
            hits.append({"type": _name, "original": m.group(0), "masked": _replacer(m)})
            return _replacer(m)
        text = pattern.sub(_repl, text)
    return text, hits


def main():
    OUT_DIR.mkdir(exist_ok=True)
    src_files = sorted(SRC_DIR.glob("*.md"))
    report = {"checked_at": "", "total_files": len(src_files), "files": []}
    total_masked = 0

    for f in src_files:
        raw = f.read_text(encoding="utf-8")
        masked, hits = desensitize(raw)
        total_masked += len(hits)
        out_name = f.name
        (OUT_DIR / out_name).write_text(masked, encoding="utf-8")
        report["files"].append({
            "file": f.name,
            "masked_count": len(hits),
            "hits": hits,
        })

    report["total_masked"] = total_masked
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"脱敏完成：扫描 {len(src_files)} 个文件，共打码 {total_masked} 处敏感信息")
    for item in report["files"]:
        if item["masked_count"]:
            print(f"  {item['file']}: {item['masked_count']} 处")
            for h in item["hits"]:
                print(f"    [{h['type']}] {h['original']} -> {h['masked']}")
    print(f"脱敏副本已输出至 {OUT_DIR}")
    print(f"检测报告：{REPORT_PATH}")


if __name__ == "__main__":
    main()