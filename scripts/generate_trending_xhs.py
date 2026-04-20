#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from html import escape, unescape
from pathlib import Path

OUT_DIR = Path.home() / ".hermes" / "output" / "trending-xhs"
REPO_ROOT = Path.home() / ".hermes" / "external-skills" / "baoyu-skills"
NODE22_BIN = Path.home() / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
MARKDOWN_TO_HTML = REPO_ROOT / "skills" / "baoyu-markdown-to-html" / "scripts" / "main.ts"
DEFAULT_FEISHU_TARGET_CHAT_ID = os.environ.get("FEISHU_TARGET_CHAT_ID", "")
TOP_N = 5
XHS_WIDTH = 1242
XHS_HEIGHT = 1660
FONT_VARIANTS = {
    "A-当前字号": {
        "badge": 30,
        "title": 62,
        "subtitle": 31,
        "chip": 24,
        "panel_title": 30,
        "li": 28,
        "footer_label": 24,
        "footer_text": 26,
    },
    "B-放大两号": {
        "badge": 32,
        "title": 66,
        "subtitle": 33,
        "chip": 26,
        "panel_title": 32,
        "li": 30,
        "footer_label": 26,
        "footer_text": 28,
    },
    "C-再放大一档": {
        "badge": 34,
        "title": 70,
        "subtitle": 35,
        "chip": 28,
        "panel_title": 34,
        "li": 32,
        "footer_label": 28,
        "footer_text": 30,
    },
}
DEFAULT_FONT_VARIANT = "C-再放大一档"
ACTIVE_FONT_VARIANTS = {DEFAULT_FONT_VARIANT: FONT_VARIANTS[DEFAULT_FONT_VARIANT]}


def load_dotenv() -> None:
    env_path = Path.home() / ".baoyu-skills" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Hermes-Agent",
            "Accept": "application/vnd.github+json, text/html, */*",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))


def parse_top_repos(limit: int = TOP_N) -> list[dict[str, str]]:
    html = fetch_text("https://github.com/trending")
    articles = re.findall(r'<article class="Box-row".*?</article>', html, re.S)
    if not articles:
        raise RuntimeError("未能从 GitHub Trending 页面解析出项目列表")
    repos: list[dict[str, str]] = []
    for rank, article in enumerate(articles[:limit], 1):
        repo_match = re.search(r'<h2[^>]*>.*?<a[^>]+href="/([^/]+/[^"#?]+)"', article, re.S)
        if not repo_match:
            continue
        desc_match = re.search(
            r'<p[^>]*class="col-9 color-fg-muted my-1 pr-4"[^>]*>(.*?)</p>',
            article,
            re.S,
        )
        desc = ""
        if desc_match:
            desc = unescape(" ".join(re.sub(r"<.*?>", " ", desc_match.group(1)).split()))
        repos.append({"rank": str(rank), "repo": repo_match.group(1).strip(), "trending_desc": desc})
    return repos


def fetch_readme(repo: str) -> str:
    for branch in ("main", "master"):
        url = f'https://raw.githubusercontent.com/{repo}/{branch}/README.md'
        try:
            text = fetch_text(url)
            if text.strip():
                return text
        except Exception:
            continue
    return ""


def extract_features(readme: str) -> list[str]:
    m = re.search(r"## Features(.*?)(?:\n## |\Z)", readme, re.S | re.I)
    if not m:
        return []
    section = m.group(1)
    features = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2 or "Feature" in cols[0] or set(cols[0]) == {"-"}:
            continue
        name = re.sub(r"[*_`]|:[^:]+:", "", cols[0]).strip()
        desc = re.sub(r"[*_`]", "", cols[1]).strip()
        features.append(f"{name}: {desc}")
    return features[:4]


def zh_topics(topics: list[str]) -> str:
    if not topics:
        return "开源、趋势项目"
    mapping = {
        "bloomberg-terminal": "Bloomberg Terminal 替代方向",
        "finance": "金融",
        "financial-markets": "金融市场",
        "investing": "投资",
        "investment-research": "投资研究",
        "machine-learning": "机器学习",
        "opensource": "开源",
        "foss": "自由开源",
        "python": "Python",
        "typescript": "TypeScript",
        "agent": "Agent",
        "ai": "AI",
    }
    out: list[str] = []
    for topic in topics:
        out.append(mapping.get(topic, topic.replace("-", " ")))
    return "、".join(out[:6])


def fetch_repo_details() -> list[dict]:
    items = parse_top_repos(TOP_N)
    repos: list[dict] = []
    for item in items:
        repo = item["repo"]
        repo_data = fetch_json(f'https://api.github.com/repos/{urllib.parse.quote(repo, safe="/")}')
        readme = fetch_readme(repo)
        repos.append(
            {
                "rank": int(item["rank"]),
                "repo": repo_data["full_name"],
                "name": repo_data["full_name"].split("/")[-1],
                "url": repo_data["html_url"],
                "homepage": repo_data.get("homepage") or repo_data["html_url"],
                "stars": repo_data.get("stargazers_count", 0),
                "language": repo_data.get("language") or "Unknown",
                "topics_zh": zh_topics(repo_data.get("topics") or []),
                "description_en": item.get("trending_desc") or repo_data.get("description") or "",
                "features_en": extract_features(readme),
            }
        )
    return repos


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 1200) -> dict:
    env = os.environ.copy()
    env["PATH"] = f'{NODE22_BIN}:{env.get("PATH", "")}'
    proc = subprocess.run(args, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=timeout)
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def extract_json_block(text: str):
    text = text.strip()
    match = re.search(r"```json\s*(.*?)\s*```", text, re.S)
    if match:
        return json.loads(match.group(1))
    for line in text.splitlines():
        line = line.strip()
        if (line.startswith("{") and line.endswith("}")) or (line.startswith("[") and line.endswith("]")):
            return json.loads(line)
    match = re.search(r"(\{[\s\S]*?\}|\[[\s\S]*?\])", text, re.S)
    if match:
        return json.loads(match.group(1))
    raise ValueError("未能从模型输出中解析 JSON")


def localize_one_repo_with_hermes(repo: dict) -> dict:
    prompt = f'''你是中文科技内容编辑。根据给定资料，输出一个严格 JSON 对象，不要解释，不要 Markdown，不要代码块。字段固定为：rank,name,repo,url,homepage,stars,language,topics_zh,one_liner_cn,highlights_cn,suitable_for_cn,verdict_cn。要求：1) one_liner_cn 24-42字，要说清楚这个项目具体是干什么的；2) highlights_cn 是长度为3的中文数组，每条尽量具体，避免空泛；3) suitable_for_cn 是长度为2的中文数组，每条都要以“适合拿来……”或“适合用来……”开头，明确能做什么事情；4) verdict_cn 必须使用这个句式："如果你的工作涉及到XXX、XXX、XXX，那就适合打开看。"，长度 22-40 字，XXX 要来自项目真实用途，不要空话；5) 除项目名、repo名、URL、技术专有名词外，避免整句英文；6) 不要杜撰没有依据的能力。资料：{json.dumps(repo, ensure_ascii=False)}'''
    result = run_command(["hermes", "chat", "-q", prompt, "--quiet"], timeout=600)
    if result["exit_code"] != 0:
        raise RuntimeError(f'Hermes 本地总结失败: {result["stderr"] or result["stdout"]}')
    data = extract_json_block(result["stdout"])
    if not isinstance(data, dict):
        raise RuntimeError("Hermes 返回的本地化 JSON 不是对象")
    return data


def localize_repos_with_hermes(repos: list[dict]) -> list[dict]:
    return [localize_one_repo_with_hermes(repo) for repo in repos]


def normalize_use_case_text(text: str) -> str:
    text = re.sub(r"^(适合拿来|适合用来)", "", text.strip())
    return text.rstrip("。；;，,")


def build_verdict_text(repo: dict) -> str:
    items = [normalize_use_case_text(x) for x in repo.get("suitable_for_cn", []) if x.strip()]
    items = [x for x in items if x]
    if items:
        joined = "、".join(items[:3])
        return f"如果你的工作涉及到{joined}，那就适合打开看。"
    fallback = repo.get("topics_zh") or repo.get("language") or repo.get("name") or "这个方向"
    if isinstance(fallback, list):
        fallback = "、".join(str(x) for x in fallback[:3])
    return f"如果你的工作涉及到{fallback}，那就适合打开看。"


def build_markdown(repos: list[dict], date_str: str) -> str:
    toplist = "\n".join([f'- Top {r["rank"]}：**{r["name"]}**（`{r["repo"]}`）' for r in repos])
    top_names = "、".join(r["name"] for r in repos)
    sections = []
    for r in repos:
        highlights = "\n".join([f'- {h}' for h in r["highlights_cn"]])
        suitable = "\n".join([f'- {h}' for h in r.get("suitable_for_cn", [])[:2]])
        sections.append(f'''## Top {r["rank"]}：{r["name"]}

- 项目：`{r["repo"]}`
- 地址：<{r["url"]}>
- 官网：<{r["homepage"]}>
- Star：{r["stars"]}
- 主要语言：{r["language"]}
- 标签：{r["topics_zh"]}

一句话看懂：

> {r["one_liner_cn"]}

亮点速看：

{highlights}

适合做什么事情：

{suitable}

一句话判断：

**{build_verdict_text(r)}**
''')
    return f'''---
title: GitHub 今日 Top5 开源项目盘点，哪一个最值得你马上收藏？
author: Hermes
description: GitHub Trending 今日 Top5：{top_names}。内容已整理为中文社交媒体风格，适合快速浏览与转发。
date: {date_str}
---

# GitHub 今日 Top5 开源项目盘点，哪一个最值得你马上收藏？

今天我把 GitHub Trending 的 **Top5** 都过了一遍，直接整理成一套方便快速判断的中文版本。

先看名单：

{toplist}

为什么要看 Top5，而不是只盯 Top1？

- 能更快判断今天的热门方向
- 能筛出真正值得收藏的项目
- 更适合做中文社交媒体盘点内容

## 今天 Top5 的整体趋势

今天的热门项目主要集中在这几个方向：

- AI Agent 与自动化
- 开发效率与工作流增强
- 工具产品化与可落地场景
- 更强调可直接上手，而不是纯演示

{chr(10).join(sections)}

## 一句话总结

**今天的 GitHub Trending，不该只看 Top1；把 Top5 一起看，判断会更完整。**

## 标签建议

#GitHubTrending #开源项目 #今日Top5 #程序员工具 #效率工具 #AIAgent #科技观察
'''


def render_html(md_path: Path) -> dict:
    return run_command(["npx", "tsx", str(MARKDOWN_TO_HTML), str(md_path), "--theme", "modern", "--color", "red"], cwd=REPO_ROOT)


def build_card_html(
    title: str,
    subtitle: str,
    chips: list[str],
    highlights: list[str],
    use_cases: list[str],
    footer: str,
    font_cfg: dict[str, int],
) -> str:
    chip_html = "".join([f'<span class="chip">{escape(c)}</span>' for c in chips[:4]])
    highlights_html = "".join([f'<li>{escape(b)}</li>' for b in highlights[:3]])
    use_case_html = "".join([f'<li>{escape(b)}</li>' for b in use_cases[:2]])
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width={XHS_WIDTH}, initial-scale=1" />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #050608; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; }}
  .card {{ width: {XHS_WIDTH}px; height: {XHS_HEIGHT}px; padding: 64px 60px 50px; background:
    radial-gradient(circle at 80% 0%, rgba(113,112,255,.22) 0%, rgba(113,112,255,0) 26%),
    radial-gradient(circle at 0% 100%, rgba(94,106,210,.18) 0%, rgba(94,106,210,0) 28%),
    linear-gradient(180deg, #07080b 0%, #0d0f14 100%);
    color: #f7f8f8; position: relative; overflow: hidden; }}
  .badge {{ display:inline-flex; align-items:center; gap:10px; padding: 12px 18px; border-radius: 999px; background: rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); color:#d0d6e0; font-size: {font_cfg['badge']}px; font-weight:600; }}
  .badge::before {{ content:''; width:10px; height:10px; border-radius:50%; background:#7170ff; box-shadow:0 0 18px rgba(113,112,255,.9); }}
  h1 {{ margin: 24px 0 12px; font-size: {max(font_cfg['title'] - 4, 58)}px; line-height: 1.04; letter-spacing: -1.25px; font-weight: 700; color:#f7f8f8; }}
  .subtitle {{ font-size: {max(font_cfg['subtitle'] - 1, 28)}px; line-height: 1.4; color:#b9c2d0; margin-bottom: 18px; }}
  .chips {{ display:flex; gap:12px; flex-wrap:wrap; margin: 0 0 20px; }}
  .chip {{ background: rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); color:#aeb7ff; border-radius:999px; padding: 8px 14px; font-size: {max(font_cfg['chip'] - 2, 20)}px; font-weight:600; }}
  .panel {{ background: rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.08); box-shadow: inset 0 0 0 1px rgba(255,255,255,.02); border-radius: 28px; padding: 22px 24px; margin-bottom: 16px; }}
  .panel h2 {{ margin:0 0 14px; font-size: {font_cfg['panel_title']}px; color:#a9b2ff; font-weight:700; }}
  ul {{ margin:0; padding-left: 1.1em; }}
  li {{ font-size: {max(font_cfg['li'] - 1, 28)}px; line-height:1.48; margin-bottom: 10px; color:#f4f7fb; }}
  .footer {{ position:absolute; left:60px; right:60px; bottom:44px; }}
  .footer-note {{ background: rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius: 22px; padding: 16px 18px; }}
  .footer-label {{ font-size: {max(font_cfg['footer_label'] - 1, 22)}px; color:#a9b2ff; margin-bottom: 7px; font-weight:700; }}
  .footer-text {{ font-size: {font_cfg['footer_text']}px; color:#dbe3ef; line-height:1.42; font-weight:600; }}
  .mono {{ position:absolute; right:60px; bottom:18px; font-family:'JetBrains Mono', ui-monospace, monospace; font-size:18px; color:#80889a; }}
</style>
</head>
<body>
  <div class="card">
    <div class="badge">GitHub 今日 Top5</div>
    <h1>{escape(title)}</h1>
    <div class="subtitle">{escape(subtitle)}</div>
    <div class="chips">{chip_html}</div>
    <div class="panel">
      <h2>这项目到底能干嘛</h2>
      <ul>{highlights_html}</ul>
    </div>
    <div class="panel">
      <h2>适合拿来做什么</h2>
      <ul>{use_case_html}</ul>
    </div>
    <div class="footer">
      <div class="footer-note">
        <div class="footer-label">一句话判断</div>
        <div class="footer-text">{escape(footer)}</div>
      </div>
    </div>
    <div class="mono">Linear 风项目卡</div>
  </div>
</body>
</html>'''


def truncate_text(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", "", text.strip()).rstrip("。；;，,")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"



def build_cover_function_line(repo: dict) -> str:
    base = repo.get("one_liner_cn") or (repo.get("highlights_cn") or [repo.get("name", "")])[0]
    base = re.sub(r"^(一个|一款|一套|一组)", "", str(base).strip())
    base = base.replace("面向", "").replace("用于", "")
    return f"Top {repo['rank']} · {repo['name']}：{truncate_text(base, 27)}"



def build_cover_use_case_line(repo: dict) -> str:
    items = [x.strip().rstrip("。；;，,") for x in repo.get("suitable_for_cn", []) if x.strip()]
    base = items[0] if items else (repo.get("topics_zh") or repo.get("name") or "值得继续深挖")
    return f"Top {repo['rank']} · {repo['name']}：{base}。"



def build_cover_card(repos: list[dict], font_cfg: dict[str, int]) -> str:
    rows = "".join(
        f'<div class="row"><div class="row-top">Top {repo["rank"]} · {escape(repo["name"])}<span>适合拿来做什么</span></div><div class="row-text">{escape(build_cover_use_case_line(repo).split("：", 1)[1])}</div></div>'
        for repo in repos[:5]
    )
    cover_title = "今天最值得立刻点开的 GitHub Top5"
    lead = "这一页不讲空话，直接告诉你今天这 5 个项目分别适合拿去干什么。"
    cover_footer = max(24, font_cfg["footer_text"] - 2)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width={XHS_WIDTH}, initial-scale=1" />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #050608; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; }}
  .card {{ width: {XHS_WIDTH}px; height: {XHS_HEIGHT}px; padding: 64px 60px 50px; background:
    radial-gradient(circle at 80% 0%, rgba(113,112,255,.22) 0%, rgba(113,112,255,0) 26%),
    radial-gradient(circle at 0% 100%, rgba(94,106,210,.18) 0%, rgba(94,106,210,0) 28%),
    linear-gradient(180deg, #07080b 0%, #0d0f14 100%);
    color: #f7f8f8; position: relative; overflow: hidden; }}
  .badge {{ display:inline-flex; align-items:center; gap:10px; padding: 12px 18px; border-radius: 999px; background: rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); color:#d0d6e0; font-size: {font_cfg['badge']}px; font-weight:600; }}
  .badge::before {{ content:''; width:10px; height:10px; border-radius:50%; background:#7170ff; box-shadow:0 0 18px rgba(113,112,255,.9); }}
  h1 {{ margin: 24px 0 12px; font-size: {max(font_cfg['title'] - 4, 58)}px; line-height: 1.04; letter-spacing: -1.25px; font-weight: 700; color:#f7f8f8; max-width:980px; }}
  .lead {{ font-size: {max(font_cfg['subtitle'] - 1, 30)}px; line-height: 1.4; color:#b9c2d0; max-width: 980px; margin-bottom: 24px; }}
  .frame {{ border:1px solid rgba(255,255,255,.08); background: rgba(255,255,255,.03); box-shadow: inset 0 0 0 1px rgba(255,255,255,.02); border-radius: 28px; padding: 24px; }}
  .panel-title {{ font-size: {font_cfg['panel_title']}px; color:#a9b2ff; font-weight:700; margin-bottom: 14px; }}
  .row {{ border-top:1px solid rgba(255,255,255,.08); padding: 13px 0 14px; }}
  .row:first-of-type {{ border-top:none; padding-top:0; }}
  .row-top {{ display:flex; align-items:center; justify-content:space-between; gap:16px; color:#eef2f6; font-size: {max(font_cfg['chip'] - 2, 20)}px; line-height:1.2; font-weight:700; margin-bottom: 8px; }}
  .row-top span {{ flex:0 0 auto; padding:5px 10px; border-radius:999px; background: rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); color:#aeb7ff; font-size:15px; font-weight:600; }}
  .row-text {{ font-size: {max(font_cfg['li'] - 1, 28)}px; line-height:1.5; color:#f4f7fb; }}
  .footer {{ position:absolute; left:60px; right:60px; bottom:44px; }}
  .footer-note {{ background: rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius: 22px; padding: 16px 18px; }}
  .footer-label {{ font-size: {max(font_cfg['footer_label'] - 1, 22)}px; color:#a9b2ff; margin-bottom: 7px; font-weight:700; }}
  .footer-text {{ font-size: {cover_footer}px; color:#dbe3ef; line-height:1.42; font-weight:600; }}
</style>
</head>
<body>
  <div class="card">
    <div class="badge">GitHub 今日 Top5</div>
    <h1>{escape(cover_title)}</h1>
    <div class="lead">{escape(lead)}</div>
    <div class="frame">
      <div class="panel-title">适合拿来做什么</div>
      {rows}
    </div>
    <div class="footer">
      <div class="footer-note">
        <div class="footer-label">一句话判断</div>
        <div class="footer-text">如果你最近在找能直接带回工作流的项目，这 5 个值得先点开再决定要不要深挖。</div>
      </div>
    </div>
  </div>
</body>
</html>'''


def build_repo_card(repo: dict, font_cfg: dict[str, int]) -> str:
    topics = repo.get("topics_zh", "")
    if isinstance(topics, list):
        topic_label = str(topics[0]) if topics else "项目"
    else:
        topic_label = str(topics).split('、')[0] if topics else "项目"
    chips = [f'Top {repo["rank"]}', repo["name"], repo["language"], topic_label]
    highlights = repo["highlights_cn"]
    use_cases = repo.get("suitable_for_cn", ["适合拿来评估这个项目是否值得继续关注。", "适合用来快速理解它的实际应用场景。"])
    return build_card_html(
        f'Top {repo["rank"]} · {repo["name"]}',
        repo["one_liner_cn"],
        chips,
        highlights,
        use_cases,
        build_verdict_text(repo),
        font_cfg,
    )


def screenshot_html(html_path: Path, image_path: Path) -> dict:
    file_url = html_path.resolve().as_uri()
    return run_command([
        "playwright", "screenshot", "--browser", "chromium", "--viewport-size", f"{XHS_WIDTH},{XHS_HEIGHT}", file_url, str(image_path)
    ], timeout=600)


def generate_card_images(repos: list[dict], run_dir: Path) -> dict:
    variants_dir = run_dir / "cards"
    variants_dir.mkdir(parents=True, exist_ok=True)
    all_outputs = []
    for variant_name, font_cfg in ACTIVE_FONT_VARIANTS.items():
        cards_dir = variants_dir / variant_name
        cards_dir.mkdir(parents=True, exist_ok=True)
        cover_html = cards_dir / "00-cover.html"
        cover_png = cards_dir / "00-cover.png"
        cover_html.write_text(build_cover_card(repos, font_cfg), encoding="utf-8")
        cover_result = screenshot_html(cover_html, cover_png)
        all_outputs.append({"variant": variant_name, "html": str(cover_html), "image": str(cover_png), "exit_code": cover_result["exit_code"], "stderr": cover_result["stderr"]})
        for repo in repos:
            html_path = cards_dir / f'{repo["rank"]:02d}-{repo["name"]}.html'
            img_path = cards_dir / f'{repo["rank"]:02d}-{repo["name"]}.png'
            html_path.write_text(build_repo_card(repo, font_cfg), encoding="utf-8")
            result = screenshot_html(html_path, img_path)
            all_outputs.append({"variant": variant_name, "html": str(html_path), "image": str(img_path), "exit_code": result["exit_code"], "stderr": result["stderr"]})
    return {"ok": all(o["exit_code"] == 0 for o in all_outputs), "outputs": all_outputs}


def feishu_post_json(url: str, data: dict, access_token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def get_feishu_token() -> str | None:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None
    resp = feishu_post_json("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", {"app_id": app_id, "app_secret": app_secret})
    return resp.get("tenant_access_token")


def feishu_upload_file(access_token: str, file_path: Path) -> str:
    import mimetypes
    import uuid

    boundary = "----HermesBoundary" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in (("file_type", "stream"), ("file_name", file_path.name)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/files",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return data["data"]["file_key"]


def feishu_send_message(access_token: str, chat_id: str, msg_type: str, content: dict) -> dict:
    return feishu_post_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        {"receive_id": chat_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
        access_token=access_token,
    )


def send_feishu_delivery(repos: list[dict], md_path: Path, html_path: Path, card_result: dict) -> dict:
    access_token = get_feishu_token()
    if not access_token:
        return {"ok": False, "reason": "missing_feishu_credentials"}
    chat_id = os.environ.get("FEISHU_TARGET_CHAT_ID") or DEFAULT_FEISHU_TARGET_CHAT_ID
    summary = "\n".join([f'Top {r["rank"]}: {r["name"]} - {r["one_liner_cn"]}' for r in repos])
    text = (
        "陛下，今日 GitHub Trending Top5 已生成完毕。\n\n"
        f"{summary}\n\n"
        f"文案：{md_path.name}\n"
        f"长文排版页：{html_path.name}\n"
        f"字号版本：{', '.join(ACTIVE_FONT_VARIANTS.keys())}\n"
        f"卡片图总数：{sum(1 for o in card_result['outputs'] if Path(o['image']).exists())} 张"
    )
    sent = {"text": feishu_send_message(access_token, chat_id, "text", {"text": text})}
    file_keys = {}
    send_files = [md_path, html_path] + [Path(o["image"]) for o in card_result["outputs"] if Path(o["image"]).exists()]
    for path_obj in send_files:
        file_key = feishu_upload_file(access_token, path_obj)
        file_keys[path_obj.name] = file_key
        feishu_send_message(access_token, chat_id, "file", {"file_key": file_key})
    return {"ok": True, "chat_id": chat_id, "file_keys": file_keys, "sent": sent}


def main() -> int:
    load_dotenv()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    slug = f"{date_str}-top5-xhs"
    run_dir = OUT_DIR / slug
    run_dir.mkdir(parents=True, exist_ok=True)

    raw_repos = fetch_repo_details()
    localized_repos = localize_repos_with_hermes(raw_repos)

    md_path = run_dir / "top5-trending-xhs.md"
    html_path = run_dir / "top5-trending-xhs.html"
    markdown = build_markdown(localized_repos, date_str)
    md_path.write_text(markdown, encoding="utf-8")

    html_result = render_html(md_path)
    card_result = generate_card_images(localized_repos, run_dir)
    feishu_delivery = send_feishu_delivery(localized_repos, md_path, html_path, card_result)

    result = {
        "ok": html_result["exit_code"] == 0 and card_result["ok"],
        "top_repos": [r["repo"] for r in localized_repos],
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "html_exists": html_path.exists(),
        "html_render_exit_code": html_result["exit_code"],
        "html_render_stdout": html_result["stdout"],
        "html_render_stderr": html_result["stderr"],
        "card_result": card_result,
        "feishu_delivery": feishu_delivery,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
