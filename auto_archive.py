# -*- coding: utf-8 -*-
"""
一键归档脚本（M8）
将"解析 → 脱敏 → 归档"串成一条命令：新增证件照片丢进 输入照片/ 目录，
运行本脚本即可自动完成全部预处理，输出解析结果、脱敏副本与检测报告。

用法：python auto_archive.py
依赖：xparse-cli（已全局安装）与同目录的 desensitize.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import desensitize as dz  # 复用脱敏模块

BASE = Path(__file__).parent
PHOTO_DIR = BASE / "输入照片"
SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".pdf"}


def xparse_invoke(args):
    """以兼容 Windows npm 的方式调用 xparse-cli（.cmd 需经 cmd.exe 执行）"""
    npm_cmd = Path.home() / "AppData" / "Roaming" / "npm" / "xparse-cli.cmd"
    prog = str(npm_cmd) if npm_cmd.exists() else "xparse-cli"
    return ["cmd", "/c", prog] + args


def step_parse():
    """第一步：为尚未解析的照片调用 xParse 解析"""
    new_count = 0
    skipped = []
    for img in sorted(PHOTO_DIR.iterdir()):
        if img.suffix.lower() not in SUPPORTED:
            continue
        target = dz.SRC_DIR / (img.stem + ".md")
        if target.exists():
            skipped.append(img.name)
            continue
        print(f"[解析] {img.name} ...")
        result = subprocess.run(
            xparse_invoke(["parse", str(img), "--api", "auto",
                           "--output", str(dz.SRC_DIR)]),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  ! 解析失败：{img.name}")
        else:
            new_count += 1
            print(f"  完成 -> {target.name}")
    return new_count, len(skipped)


def step_desensitize():
    """第二步：对全部解析结果执行敏感信息脱敏"""
    total_hits = 0
    file_hits = 0
    for f in sorted(dz.SRC_DIR.glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        masked, hits = dz.desensitize(raw)
        (dz.OUT_DIR / f.name).write_text(masked, encoding="utf-8")
        if hits:
            total_hits += len(hits)
            file_hits += 1
    # 更新检测报告
    report = {
        "checked_at": "",
        "total_files": len(list(dz.SRC_DIR.glob("*.md"))),
        "files": [],
        "total_masked": total_hits,
    }
    # 逐文件重写报告条目（保留命中详情）
    for f in sorted(dz.SRC_DIR.glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        _, hits = dz.desensitize(raw)
        report["files"].append({
            "file": f.name, "masked_count": len(hits), "hits": hits,
        })
    dz.REPORT_PATH.write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return file_hits, total_hits


def main():
    print("=== 拾光档案 · 一键归档 ===")
    print()
    print("第一步：解析新增照片")
    new_count, skip_count = step_parse()
    print(f"  新增解析 {new_count} 张，跳过已解析 {skip_count} 张")
    print()
    print("第二步：敏感信息自动脱敏")
    file_hits, total_hits = step_desensitize()
    print(f"  含敏感信息文件 {file_hits} 个，共打码 {total_hits} 处")
    print()
    print("归档完成。脱敏副本位于 脱敏结果/，检测报告见 脱敏报告.json")
    print("下一步：由 AI 将解析文本提炼为经历卡片，存入 经历卡片/ 并汇入 archive.json")


if __name__ == "__main__":
    main()