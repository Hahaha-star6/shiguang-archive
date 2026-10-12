# -*- coding: utf-8 -*-
"""
识别规则离线回归测试（不消耗 xParse 额度）

用途：每次修改 server.py 里的 guess_title / guess_org / guess_date /
guess_summary / guess_highlights 规则后，双击运行本脚本（或命令行
python tests\\regress_ocr.py），对「解析结果」文件夹里所有已保存的
真实 OCR 原文重新提取一遍，人工核对输出有没有退化。

工作原理：xParse 每次识别都会在 解析结果/ 存一份 .md 原文，
本脚本直接喂这些原文给规则函数，不调用网络 API。
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import desensitize as dz  # noqa: E402

SRC = ROOT / "解析结果"


def main():
    files = sorted(SRC.glob("*.md"))
    if not files:
        print("解析结果 文件夹里还没有 .md 原文（先上传几张证书识别一次即可生成）。")
        return
    for md in files:
        raw = md.read_text(encoding="utf-8")
        masked, hits = dz.desensitize(raw)
        text = server.clean_best(masked)
        cat = server.guess_category(text)
        org = server.guess_org(masked)
        title = server.guess_title(masked)
        hl = server.guess_highlights(cat, org, text)
        print(f"### {md.name}")
        print("  title   =", title)
        print("  org     =", org or "（空）")
        print("  date    =", server.guess_date(text, masked) or "（空）")
        print("  cat     =", cat, "| highlights =", "/".join(hl) if hl else "（空）")
        print("  summary =", server.guess_summary(masked, title=title)[:90])
        print(f"  masked  = {len(hits)} 处", ("（前端会回退为「XX已脱敏」标签）" if hits and not hl else ""))
        print()
    print(f"共回归 {len(files)} 份识别原文。如发现某张证书字段变差，请保留该 .md 并调整规则后重跑本脚本。")


if __name__ == "__main__":
    main()
