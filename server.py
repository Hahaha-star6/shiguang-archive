# -*- coding: utf-8 -*-
"""
拾光档案 · 本地服务
功能：
  1. 静态托管「网页档案馆」（index.html / resume.html）
  2. POST /api/upload —— 上传证书照片 → 自动执行「xParse 解析 → 敏感信息脱敏」
     → 生成经历卡片草稿（标题/分类/日期/摘要建议），返回给网页预填

双击 start.bat 即可启动；启动后会自动用浏览器打开档案馆首页。
"""
import hashlib
import json
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs, quote

BASE = Path(__file__).parent
PHOTO = BASE / "输入照片"
SRC = BASE / "解析结果"
OUT = BASE / "脱敏结果"
REPORT = BASE / "脱敏报告.json"
PHOTO_REG_FILE = BASE / "上传照片注册.json"  # 照片哈希登记表：同一张照片重复上传可被识别
PORT = 8765
MAX_UPLOAD = 20 * 1024 * 1024  # 20 MB

sys.path.insert(0, str(BASE))
import desensitize as dz

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".pdf"}


def looks_like_media(body: bytes, suffix: str) -> bool:
    """校验上传内容的前几字节是否符合声明格式（文件头魔数），
    防止把改名的文本/损坏文件当图片解析，产生垃圾卡片。"""
    if suffix == ".pdf":
        return body.startswith(b"%PDF-")
    if suffix in (".jpg", ".jpeg"):
        return body.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    if suffix == ".bmp":
        return body.startswith(b"BM")
    if suffix in (".tif", ".tiff"):
        return body[:2] in (b"II", b"MM")
    return True   # 未知格式兜底放行（前面已兜底为 .png）

# 分类建议规则 —— 分两轮：
# 第一轮看「内容强信号」（证书讲的是什么事），第二轮才看落款组织词（团委/学生会），
# 避免志愿者证书因落款"河北金融学院团委"被误判成「组织经历」
CAT_STRONG = [
    (["志愿", "公益"], "志愿公益"),
    (["实践", "实习", "见习"], "实习实践"),
    (["四级", "六级", "CET", "普通话", "资格", "从业", "会计", "驾驶证", "计算机等级"], "技能证书"),
    (["等奖", "大赛", "竞赛", "奖学金", "三好", "标兵"], "荣誉奖项"),
]
CAT_ORG = [
    (["聘", "学生会", "社团", "部长", "主席", "干事"], "组织经历"),
    (["优秀", "荣誉"], "荣誉奖项"),
]

# 日期提取（①完整中文日期 ②紧凑数字；③OCR 中文数字「二0二六年七月」）
DATE_RES = [
    re.compile(r"(20\d{2})年(\d{1,2})月(?:\d{1,2}[日号])?"),  # 2025年9月 / 2025年9月15日
    re.compile(r"(20\d{2})[-./](\d{1,2})(?![\d-])"),          # 2025-09 / 2025.9
]
CN_NUM = {"〇": "0", "零": "0", "0": "0", "O": "0", "o": "0", "一": "1", "二": "2", "两": "2",
          "三": "3", "四": "4", "五": "5", "六": "6", "七": "7",
          "八": "8", "九": "9"}
# 中文数字日期也兼容形近字：「〇」常被识别成字母 O/o（如 二O二四年 → 2024）
CN_DATE_RES = re.compile(r"([二2〇零0Oo一二两三四五六七八九]{4})年(十[一二两]?|[一二三四五六七八九])月")
CN_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
            "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
}


def guess_category(text):
    # 第一轮：内容强信号（志愿/竞赛/资格等）优先
    for words, cat in CAT_STRONG:
        if any(w in text for w in words):
            return cat
    # 第二轮：才认落款组织词（聘书/学生会/团委系统）
    for words, cat in CAT_ORG:
        if any(w in text for w in words):
            return cat
    return "荣誉奖项"


# 证书上的「泛称标题」（所有证书都印的门面字，不能当作经历标题）
GENERIC_TITLES = {
    "荣誉证书", "获奖证书", "荣誉称号证书", "聘书", "聘用证书", "结业证书",
    "毕业证书", "学位证书", "资格证书", "成绩报告单", "成绩单",
    "服务证书", "社会实践证书",
}
GENERIC_TITLES_EN = {"HONORARY CREDENTIAL", "CERTIFICATE", "CERTIFICATE OF HONOR", "DIPLOMA"}
# 网页截图里的导航噪声（整行或以这些词开头都算）
NAV_NOISE = {"首页", "考试资讯", "公示公告", "成绩查询", "首页＞成绩查询"}
NAV_PREFIX = ("考试项目",)
# 奖项/职务提取句式（命中即用，最贴近「这张证到底证明了什么」；在「去空白全文」上匹配，跨行也能命中）
AWARD_PATTERNS = [
    re.compile(r"被评为[「『【\"“']?(.+?)[」』】\"”']?[。.，,；;]"),
    re.compile(r"被授予(.+?)称号"),
    re.compile(r"荣获[「『【\"“']?(.+?)[」』】\"”']?[。.，,；;]"),
    re.compile(r"担任(.+?)[，,。.]?任期"),
    re.compile(r"聘请[\u4e00-\u9fa5]{2,4}(?:同学|同志|先生|女士)?担任(.+?)[，,。.]"),
]
FIELD_WORDS = (
    r"姓\s*名|性\s*别|证件号码|证书编号|成绩报告单编号|准考证号|考试级别|取得时间|"
    r"校发机构|颁发机构|发证机关|学\s*校|专\s*业|学\s*号|入学年份|有效期至"
)
# 纯字段标签行（标签独占一行、没有值，OCR 常把标签和值拆成两行）
FIELD_LABEL_ONLY = re.compile(r"^(?:" + FIELD_WORDS + r")[：:]?$")
# 标签带值行（如「证书编号：2024KJ0012345」）——不当标题，但摘要里保留
FIELD_WITH_VALUE = re.compile(r"^(?:" + FIELD_WORDS + r")[：:].+$")
PERSON_LINE = re.compile(r"^[\u4e00-\u9fa5]{2,4}(同学|同志|先生|女士)?[：:]$")
# 带引号的项目名（如“桑梓计划”“返家乡”），拼成「大项目·子项目」做标题兜底
QUOTE_PATTERN = re.compile(r"[“「『\"]([\u4e00-\u9fa5][\u4e00-\u9fa5·\-0-9]{1,20})[”」』\"]")
# 引号后可补全的项目类型词（“返家乡”暑期社会实践 → 标题补出「社会实践」）
TITLE_SUFFIX_PATTERN = re.compile(r"[”」』\"][\u4e00-\u9fa5]{0,6}?(社会实践|志愿服务|志愿活动|竞赛|大赛|大学生专题)")

# ---------- 颁发单位（org）提取 ----------
# 「颁发单位：XXX」这类字段标签
ORG_FIELD = re.compile(r"(?:颁发单位|校发机构|发证机关|颁发机构|发证单位|授予单位|院校)[：:]\s*([^\s，,。；;]{2,40})")
# 以机构后缀结尾的落款行（如 中共张家口市委组织部 / 河北金融学院团委 / 校团委学生会）
ORG_LINE = re.compile(
    r"^[\u4e00-\u9fa5]{2,30}?(?:人力资源和社会保障局|组织部|宣传部|办公室|财政局|教育局|委员会|"
    r"局|部|处|中心|大学|学院|学校|区委|团区委|团市委|团委|学生会|社团|组委会|协会|人民政府|政府|工委)$"
)
# 正文动词/叙事词：正文行可能恰好以「学院」「大学」结尾，含这些词的一律不算机构落款
BODY_VERB_RE = re.compile(r"(担任|聘请|荣获|评为|参加|表现|圆满|出色|积极|服务|实践|竞赛|考试|获得|授予|证书)")


def looks_org(l):
    """判断一行是否为机构落款（后缀匹配且不是正文句）"""
    return bool(ORG_LINE.match(l)) and not BODY_VERB_RE.search(l)
# 套话行（特颁此证/特此聘用等）与持证人行（含姓名，且不是内容）
COURTESY_RE = re.compile(r"^(?:特[颁发]此证|特发此状|特颁此状|特此聘用|特此证明)")
HOLDER_RE = re.compile(r"^(?:获奖者|获得者|持证人|受证人|受聘人|获证人)[：:]")
# 纯日期行（摘要里会被日期字段取代）
DATE_ONLY_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5二0-9]{1,12}年[一二三四五六七八九十\d]{0,3}月[\u4e00-\u9fa5\d]{0,4}日?|"
    r"[二0-9]{4}年?[一二三四五六七八九十\d]{0,3}月?日?|"
    r"\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?|\d{4}\s*[-—]\s*\d{4}\s*学年?)$"
)


# OCR 常见错字修正：只改「高置信度的固定错字」（形近字误认），不做泛化猜测
OCR_FIX = {
    "张家世市": "张家口市",
    "长家口市": "张家口市",
    "接长": "校长",      # 书法体「校长」常被误读为「接长」（如：校长奖学金证书）
    "授子": "授予",      # 「兹授予」的「予」常被误读为「子」
}


def clean_md(text, keep_comments=False):
    """把 xParse 输出的 Markdown 清成干净纯文本（仅用于标题/摘要/预览展示；解析原文件仍保留原貌）。
    keep_comments=True 时把 HTML 注释里的文字当作正文保留——
    个别证书（如桑梓计划服务证书）的正文被 xParse 整个放进注释块，删注释等于删全文。"""
    t = text
    for bad, good in OCR_FIX.items():
        t = t.replace(bad, good)
    if keep_comments:
        t = re.sub(r"<!--([\s\S]*?)-->", r"\n\1\n", t)   # 注释里的文字可能是正文
    else:
        t = re.sub(r"<!--[\s\S]*?-->", "\n", t)          # 默认按噪声处理
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)            # 内嵌图片链接
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)        # 普通链接只留文字
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)   # 标题井号
    t = t.replace("**", "").replace("`", "").replace("☒", "")
    t = t.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
    t = re.sub(r"(姓)[ \t　]+(名)", r"\1\2", t)           # OCR 常把「姓名」拆成「姓 名」
    t = re.sub(r"\$[ \t\-0-9]*\$?", " ", t)               # OCR 表格噪声，如「-3 $-3-$」
    t = re.sub(r"[ \t　]+-?\d{1,3}[ \t　]+(?=[\u4e00-\u9fa5])", " ", t)  # 中文前的孤立数字噪声
    t = re.sub(r"(?<=[\u4e00-\u9fa5])[ \t　]+(?=[\u4e00-\u9fa5])", "", t)  # 接合同行内被 OCR 拆开的中文词
    t = re.sub(r"[ \t　]+", " ", t)
    out = []
    for l in (x.strip() for x in t.splitlines()):
        if not l:
            continue
        if out and out[-1] == l:        # OCR 常见的整行重复
            continue
        out.append(l)
    return "\n".join(out)


def clean_best(text):
    """先按「注释=噪声」清洗；若正文所剩中文极少，说明正文藏在注释块里，换「保留注释」模式再洗一遍"""
    primary = clean_md(text)
    if len(re.findall(r"[\u4e00-\u9fa5]", primary)) < 6:
        return clean_md(text, keep_comments=True)
    return primary


def _is_noise_line(l, for_summary=False):
    up = l.upper()
    if l in GENERIC_TITLES or up in GENERIC_TITLES_EN or l in NAV_NOISE:
        return True
    if any(l.startswith(p) for p in NAV_PREFIX):
        return True
    if PERSON_LINE.match(l) or FIELD_LABEL_ONLY.match(l):
        return True
    if not for_summary and FIELD_WITH_VALUE.match(l):
        return True   # 「标签：值」行不能当标题，但摘要里是有效信息
    if re.fullmatch(r"[A-Za-z\s>&]+", l):   # 纯英文导航/碎片
        return True
    return False


def _strip_lead_noise(l):
    """去掉行首的证书泛称和英文噪声，露出真正的中文正文（数字可能是年份，保留）"""
    for g in sorted(GENERIC_TITLES, key=len, reverse=True):
        if l.startswith(g):
            l = l[len(g):].lstrip(" ：:·-—")
            break
    m = re.match(r"^[A-Za-z\s>&$°•·.]+(?=[\u4e00-\u9fa5])", l)
    if m:
        l = l[m.end():]
    return l.strip(" ：:·-—")


def guess_title(text):
    clean = clean_best(text)
    # ① 优先从「被评为/荣获/担任…」句式里抽真正的奖项名或职务名
    #    在去掉全部空白的全文上匹配，OCR 把一句话断成两三行也能命中
    flat = re.sub(r"\s+", "", clean)
    for pat in AWARD_PATTERNS:
        m = pat.search(flat)
        if m:
            cand = m.group(1).strip(" 「『【\"“”’」』】】.。，,、")
            if 2 <= len(cand) <= 24:
                return cand
    # ② 再收带引号的项目名（如“桑梓计划”“返家乡”），拼成「项目·子项」，并尝试补全类型词
    qs = QUOTE_PATTERN.findall(flat)
    if qs:
        keep = [q for q in qs if not any(w in q for w in ("证书", "同学", "先生", "女士", "同志"))]
        if keep:
            joined = "·".join(dict.fromkeys(keep))
            tails = list(TITLE_SUFFIX_PATTERN.finditer(flat))
            if tails:
                tail = tails[-1]   # 取最后一个引号后的类型词（“桑梓计划”…“返家乡”暑期社会实践 → 社会实践）
                if len(joined) + len(tail.group(1)) <= 24:
                    return joined + tail.group(1)
            return joined[:24]
    # ③ 逐行找第一个有实质内容的行（跳过门面泛称、人名、字段标签、网页导航）
    for l in clean.splitlines():
        l = l.strip(" ：:·-—")
        if len(l) < 4 or _is_noise_line(l):
            continue
        l = _strip_lead_noise(l)
        if len(l) < 4:
            continue
        return l[:24]
    return "未命名证书"


def guess_org(text):
    """颁发单位：优先「颁发单位：XXX」字段，其次收集文末的机构落款行（去掉机构代码等尾巴），用「、」连接"""
    clean = clean_best(text)
    m = ORG_FIELD.search(clean)
    primary = m.group(1).strip() if m else ""
    orgs = []
    for l in clean.splitlines():
        l = re.sub(r"\s*[0-9]{6,}\s*$", "", l).strip(" ：: ")
        if not looks_org(l):
            continue
        # 学位证书里「学位评定委员会」隶属于大学（院校字段已作为主单位），不再重复列出
        if primary and l == "学位评定委员会":
            continue
        if primary and (primary in l or l in primary):
            continue   # 与字段值近似重复（如「燕京理工大学」vs「燕京理工大学教务处」）
        if l not in orgs:
            orgs.append(l)
    if primary and primary not in orgs:
        orgs.insert(0, primary)
    return "、".join(orgs)


def guess_highlights(category, org, text):
    """从分类/颁发单位推导「亮点」标签（如 政务实践 / 三部门联合发证 / 志愿服务）"""
    orgs = [o for o in re.split(r"[、，,]", org) if o.strip()]
    hs = []
    if category in ("实习实践", "组织经历") and any(
        w in o for o in orgs for w in ("组织部", "政府", "局", "委员会", "党委", "团委")
    ):
        hs.append("政务实践")
    n = len(orgs)
    if n == 2:
        hs.append("双单位联合颁发")
    elif n == 3:
        hs.append("三部门联合发证")
    elif n > 3:
        hs.append("多部门联合发证")
    if "志愿" in text:
        hs.append("志愿服务")
    if category == "荣誉奖项" and any(w in text for w in ("三好", "标兵", "优秀")):
        hs.append("校级表彰" if any(w in o for o in orgs for w in ("学院", "大学", "学校")) else "表彰荣誉")
    return hs[:3]


def guess_summary(text, limit=80):
    """摘要：只保留正文叙述句——去掉门面套话、持证人行、颁发单位行与纯日期行（它们已单独成字段），
    去掉引号装饰与「你在…」这类招呼语，按句号自然截断"""
    clean = clean_best(text)
    lines = []
    for l in clean.splitlines():
        l2 = re.sub(r"(?<=[\u4e00-\u9fa5])\s*[0-9]{6,}\s*$", "", _strip_lead_noise(l)).strip()  # 中文机构名后的代码尾巴（不动证书编号）
        if len(l2) < 2 or _is_noise_line(l, for_summary=True):
            continue
        if COURTESY_RE.match(l2) or HOLDER_RE.match(l2):
            continue
        if looks_org(l2) or ORG_FIELD.search(l2) or DATE_ONLY_RE.match(l2):
            continue
        lines.append(l2)
    # 智能拼行：正文断行直接相接；「标签：值」行之间保留空格
    s = ""
    for l2 in lines:
        if s and (
            "：" in s[-6:] or "：" in l2
            or l2.startswith(("笔试成绩", "总分", "听力", "阅读", "写作", "颁发"))
            or re.search(r"[。！!？]$", s)
        ):
            s += " "
        s += l2
    s = s.replace("“", "").replace("”", "").replace("「", "").replace("」", "").replace("【", "").replace("】", "")
    s = re.sub(
        r"^兹聘请(?:[\u4e00-\u9fa5]{2,4}(?:同学|同志|先生|女士)|[\u4e00-\u9fa5]{2,4})", "", s
    )
    s = re.sub(r"^(?:您在|你在|兹有|兹证明|该生|该同学)", "", s)
    s = re.sub(r"^担任", "", s)
    s = re.sub(r"(?<=[。！!])[ \t　]*特[颁发]此证[，,]?以资鼓励[。！!]?", "", s)   # 句中的套话尾
    # 句尾若还挂着一串机构落款（正文与落款同段时），一并去掉
    s = re.sub(r"(?<=[。！!])\s*[\u4e00-\u9fa5、 ]{4,60}(?:委员会|组织部|人力资源和社会保障局|人社局|团委|学生会|学院|大学|学校|中心|人民政府|政府|工委|市委|区委|局|部|处)\s*$", "", s)
    s = re.sub(r"[，,]\s*特[颁发]此证[，,]?以资鼓励[。！!]?$", "。", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit]
        cut = max(s.rfind("。"), s.rfind("！"), s.rfind("？"))
        if cut > limit // 2:
            s = s[: cut + 1]
        else:
            sp = s.rfind(" ", 0, limit)
            if sp > limit // 2:
                s = s[:sp]   # 没有完整句子时，退回到空格处截断，避免截断字段值
    return s


def guess_date(text):
    for pat in DATE_RES:
        m = pat.search(text)
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
    # OCR 中文数字日期：如「二0二六年七月十五日」
    m = CN_DATE_RES.search(text)
    if m:
        year = "".join(CN_NUM.get(ch, ch) for ch in m.group(1))
        if year.isdigit() and year.startswith(("19", "20")):
            month = CN_MONTH.get(m.group(2).replace("两", "二"))
            if month:
                return f"{year}-{month:02d}"
    # 学年区间：2023-2024学年
    m = re.search(r"(20\d{2})\s*[-—~至]\s*(20\d{2})\s*学年", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # 只有年份：2026年（证书常只印年份）
    m = re.search(r"(20\d{2})年", text)
    if m:
        return m.group(1)
    return ""


def build_report(files_hits, total):
    report = {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(files_hits),
        "files": files_hits,
        "total_masked": total,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def xparse_invoke(args):
    """以兼容 Windows npm 的方式调用 xparse-cli（.cmd 需经 cmd.exe 执行）"""
    npm_cmd = Path.home() / "AppData" / "Roaming" / "npm" / "xparse-cli.cmd"
    prog = str(npm_cmd) if npm_cmd.exists() else "xparse-cli"
    return ["cmd", "/c", prog] + args


def do_recognize(saved: Path, dbg):
    """对上传文件执行 解析→脱敏→卡片草稿 全流程，返回结果字典"""
    md_target = SRC / (saved.stem + ".md")
    r = subprocess.run(
        xparse_invoke(["parse", str(saved), "--api", "auto", "--output", str(SRC)]),
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0 or not md_target.exists():
        dbg.append(r.stdout + r.stderr)
        raise RuntimeError("xParse 解析失败，请确认图片清晰且格式受支持")

    raw = md_target.read_text(encoding="utf-8")
    masked, hits = dz.desensitize(raw)
    (OUT / md_target.name).write_text(masked, encoding="utf-8")
    build_report([{"file": md_target.name, "masked_count": len(hits), "hits": hits}], len(hits))

    text = clean_best(masked)   # 干净文本：去 Markdown 标记/图片链接/OCR 噪声，正文藏在注释块时自动兜底
    org = guess_org(masked)
    return {
        "ok": True,
        "file": saved.name,
        "title": guess_title(masked),
        "category": guess_category(text),
        "org": org,
        "date": guess_date(text),
        "summary": guess_summary(masked),
        "highlights": guess_highlights(guess_category(text), org, text),
        "mask_count": len(hits),
        "mask_hits": hits,
        "raw_text": text,
    }


# ---------- 身份信息识别（学生证 / 校园卡） ----------

ID_PATTERNS = {
    "name": re.compile(r"姓\s*名[:：]\s*([\u4e00-\u9fa5]{2,4})"),
    "school": re.compile(r"([\u4e00-\u9fa5]{2,20}(?:大学|学院|学校))"),
    "major": re.compile(r"专\s*业[:：]\s*([\u4e00-\u9fa5A-Za-z0-9()（）]{2,20})"),
    "student_id": re.compile(r"学\s*号[:：]\s*([0-9A-Za-z()（）]{6,20})"),
    "enroll": re.compile(r"入学\s*年份[:：]?\s*(20\d{2})年\s*(\d{1,2})?月?"),
    "valid": re.compile(r"有效期至[:：]?\s*(20\d{2})年\s*(\d{1,2})?月?"),
}


def mask_sid(sid: str) -> str:
    """学号打码：保留前 4 位和后 2 位，中间打码"""
    sid = (sid or "").strip()
    if len(sid) >= 8:
        return sid[:4] + "*" * (len(sid) - 6) + sid[-2:]
    return (sid[:2] + "***") if len(sid) > 2 else "***"


def extract_identity(text):
    """从解析文本中抽取身份字段，附带人工核对提示"""
    def grab(pattern, default=""):
        m = pattern.search(text)
        if not m:
            return default
        return re.sub(r"[\s：:]+", "", m.group(1))

    warnings = []
    sid_raw = grab(ID_PATTERNS["student_id"])
    sid_clean = re.sub(r"[^0-9A-Za-z]", "", sid_raw)
    if sid_raw and sid_clean != sid_raw:
        warnings.append(f"学号识别结果含特殊字符（{sid_raw}），已自动清理为 {sid_clean}，请核对")

    enroll, valid = "", ""
    m = ID_PATTERNS["enroll"].search(text)
    if m:
        enroll = f"{m.group(1)}-{int(m.group(2) or 9):02d}"
    m = ID_PATTERNS["valid"].search(text)
    if m:
        valid = f"{m.group(1)}-{int(m.group(2) or 6):02d}"

    name = grab(ID_PATTERNS["name"])
    school = grab(ID_PATTERNS["school"])
    major = grab(ID_PATTERNS["major"])
    if not name:
        warnings.append("未识别到姓名，请手动填写")
    return {
        "name": name,
        "school": school,
        "major": major,
        "student_id_clean": sid_clean,
        "student_id_masked": mask_sid(sid_clean),
        "enroll": enroll,
        "valid_until": valid,
        "warnings": warnings,
    }


# ---------- 自定义简历模板识别（上传模板截图 → 识别板块结构） ----------

TPL_KEYWORDS = [
    "教育背景", "实习经历", "工作经历", "项目经历", "校园经历",
    "技能", "自我评价", "个人简介", "求职意向", "联系方式",
    "荣誉", "获奖", "证书", "主修课程", "GPA", "期望薪资",
]


def do_template(saved: Path, dbg):
    """模板截图 解析→识别板块关键词（配色提取在前端 canvas 完成）"""
    md_target = SRC / (saved.stem + ".md")
    r = subprocess.run(
        xparse_invoke(["parse", str(saved), "--api", "auto", "--output", str(SRC)]),
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0 or not md_target.exists():
        dbg.append(r.stdout + r.stderr)
        raise RuntimeError("模板截图识别失败，请换一张更清晰的图片")

    raw = md_target.read_text(encoding="utf-8")
    keywords = [k for k in TPL_KEYWORDS if k in raw]
    return {
        "ok": True,
        "file": saved.name,
        "keywords": keywords,
        "raw_text": raw[:1500],
    }


def do_identity(saved: Path, dbg):
    """学生证/校园卡 解析→脱敏→身份字段抽取"""
    md_target = SRC / (saved.stem + ".md")
    r = subprocess.run(
        xparse_invoke(["parse", str(saved), "--api", "auto", "--output", str(SRC)]),
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0 or not md_target.exists():
        dbg.append(r.stdout + r.stderr)
        raise RuntimeError("xParse 解析失败，请确认学生证照片清晰且方向摆正")

    raw = md_target.read_text(encoding="utf-8")
    masked, hits = dz.desensitize(raw)
    (OUT / md_target.name).write_text(masked, encoding="utf-8")
    build_report([{"file": md_target.name, "masked_count": len(hits), "hits": hits}], len(hits))

    ident = extract_identity(masked)
    return {
        "ok": True,
        "file": saved.name,
        **ident,
        "mask_count": len(hits),
        "mask_hits": hits,
        "raw_text": masked,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ShiguangArchives/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 允许本地页面直接双击打开(file://)时也能调用本服务
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # CORS 预检：浏览器对 application/octet-stream 的 POST 会先发 OPTIONS
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        rel = unquote(parsed.path).lstrip("/")
        if rel in ("", "index.html"):
            self.send_response(302)
            # HTTP 响应头只能用拉丁字母，中文路径必须先 URL 编码，否则服务直接崩
            self.send_header("Location", quote("/网页档案馆/index.html"))
            self.end_headers()
            return
        target = (BASE / rel).resolve()
        try:
            target.relative_to(BASE.resolve())
        except ValueError:
            self._send_json({"ok": False, "error": "非法路径"}, 403)
            return
        if not target.is_file():
            self._send_json({"ok": False, "error": "404"}, 404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/upload", "/api/student-card", "/api/template-style"):
            self._send_json({"ok": False, "error": "接口不存在"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_UPLOAD:
            self._send_json({"ok": False, "error": "文件为空或超过 20MB"}, 400)
            return
        q = parse_qs(parsed.query)
        orig_name = unquote(q.get("name", [""])[0])
        suffix = Path(orig_name).suffix.lower() if orig_name else ""
        if suffix not in ALLOWED_EXT:
            suffix = ".png"  # 兜底按图片处理
        body = self.rfile.read(length)
        # 文件真实格式体检：防止把改名的文本/损坏文件当图片解析，产生垃圾卡片
        if not looks_like_media(body, suffix):
            self._send_json({"ok": False, "error": "上传的文件不是有效的图片或 PDF，请换成清晰的照片"}, 400)
            return
        PHOTO.mkdir(exist_ok=True)

        # 证书照片：同一张照片（内容哈希一致）重复上传 → 复用已存原件并标记 dup，
        # 方便前端提示「这张照片已归档过，可能重复」
        dup = False
        hash_key = ""
        if parsed.path == "/api/upload":
            hash_key = hashlib.sha256(body).hexdigest()
            try:
                # utf-8-sig：兼容可能带 BOM 的外部编辑（如 PowerShell 写出的 UTF-8），避免解析失败导致登记表被重置
                reg = json.loads(PHOTO_REG_FILE.read_text(encoding="utf-8-sig"))
            except Exception:
                reg = {}
            reg = dict(reg)
            if reg.get(hash_key) and (PHOTO / reg[hash_key]).is_file():
                saved = PHOTO / reg[hash_key]
                dup = True
            else:
                saved = PHOTO / f"upload_{datetime.now():%Y%m%d_%H%M%S_%f}{suffix}"
                saved.write_bytes(body)
                reg[hash_key] = saved.name
                try:
                    PHOTO_REG_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        else:
            saved = PHOTO / f"upload_{datetime.now():%Y%m%d_%H%M%S_%f}{suffix}"
            saved.write_bytes(body)

        dbg = []
        try:
            if parsed.path == "/api/student-card":
                result = do_identity(saved, dbg)
            elif parsed.path == "/api/template-style":
                result = do_template(saved, dbg)
            else:
                result = do_recognize(saved, dbg)
                # 原件照片自动归档（保存在本机「输入照片」文件夹，卡片上有缩略图可点开大图）
                result["photo"] = f"/输入照片/{saved.name}"
                result["dup"] = dup
            self._send_json(result)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e), "detail": dbg[-1] if dbg else ""}, 500)


def main():
    url = f"http://127.0.0.1:{PORT}{quote('/网页档案馆/index.html')}"
    # 端口被占时不要直接报错：先「敲敲门」，若自家服务其实已经在运行，就直接开浏览器
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        import urllib.request
        try:
            # 能拿到响应（含 302 跳转）就说明 8765 上跑的正是本服务
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            print("检测到本地服务已经在运行（可能是之前的窗口还开着），直接帮你打开浏览器。")
            print("地址：", url)
            webbrowser.open(url)
            input("按回车键关闭本提示窗口（不会影响已经在运行的服务）…")
            return
        except Exception:
            print(f"[启动失败] 端口 {PORT} 被其他程序占用了，拾光档案服务无法启动。")
            print("请关掉占用该端口的程序（通常是之前没关的黑色服务窗口），再重新双击 start.bat。")
            input("按回车键退出…")
            sys.exit(1)
    # 中文路径先编码成 %XX 形式，保证任何浏览器都能正确打开
    print("拾光档案已启动：", url)
    print("浏览器应已自动打开；如未打开，请手动复制上面的网址。")
    print("使用期间请保持本窗口开着，关闭窗口即停止服务。")
    threading.Timer(0.8, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()