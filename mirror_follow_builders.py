#!/usr/bin/env python3
"""成章海外订阅镜像 · Mac mini 采集器（Follow Builders → Atom → Gitee）

背景：成章服务器（火山引擎国内机）连不通 raw.githubusercontent.com，
Follow Builders 上游的 X／博客／播客三份 feed 由本机经代理读取，
转成 Atom（builders-x.xml / builders-blogs.xml / builders-podcasts.xml）
推送到本仓库；成章服务器按「海外订阅 · 自己补充」的 RSS 条目从
Gitee raw 地址收取，按 remote_id 去重。

与服务器端 mige_intake/radar_inbox/sources.py 的字段语义保持一致：
X 展开 account.tweets；博客／播客每条目即一条；body 放正文或转写。
只镜像，不做筛选；筛选在成章侧按来源规则执行。

通常不需要直接运行：collect_youtube_radar.py 每次跑完会自动调用本脚本。
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPT = HERE / "last_mirror_receipt.json"
UPSTREAM = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-{kind}.json"
KINDS = (("x", "builders-x.xml", "海外订阅 · X · Mac mini 镜像"),
         ("blogs", "builders-blogs.xml", "海外订阅 · 博客 · Mac mini 镜像"),
         ("podcasts", "builders-podcasts.xml", "海外订阅 · 播客 · Mac mini 镜像"))
PER_KIND_LIMIT = 30   # 每类镜像上限；服务器每源每轮取最新 5，按 remote_id 去重滚动补齐
SCOPE = "Follow Builders 保存的帖子／文章／转写（Mac mini 镜像）；完整性以其提供内容为准，未独立核实"


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def fetch_upstream(kind, proxy):
    request = urllib.request.Request(UPSTREAM.format(kind=kind),
                                     headers={"User-Agent": "Mige-Radar-Mirror/1.0", "Accept": "application/json"})
    if proxy and proxy != "direct":
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        response = opener.open(request, timeout=45)
    else:
        response = urllib.request.urlopen(request, timeout=45)
    with response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000:
        raise RuntimeError("上游内容超过读取范围")
    payload = json.loads(data)
    if not isinstance(payload.get(kind), list):
        raise RuntimeError("上游返回格式变化")
    return payload[kind]


def flatten(kind, accounts):
    """与服务器 sources.py 的展开规则一致，输出统一条目。"""
    rows = []
    for account in accounts:
        name = account.get("name") or "未命名"
        entries = account.get("tweets", []) if kind == "x" else [account]
        for d in entries:
            url = d.get("url") or ""
            title = d.get("title") or ((d.get("text") or "").split("\n")[0][:180] or "未命名帖子")
            guid = str(d.get("id") or d.get("guid") or (name + "|" + url + "|" + title))
            rows.append({"guid": guid, "title": title, "link": url,
                         "author": name,
                         "published": d.get("createdAt") or d.get("publishedAt") or "",
                         "summary": d.get("summary") or d.get("description") or ""})
    rows.sort(key=lambda r: r["published"], reverse=True)
    return rows[:PER_KIND_LIMIT]


def build_atom(rows, feed_id, title):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<feed xmlns="http://www.w3.org/2005/Atom">',
             f'<id>tag:chengzhang.amilakids.com,2026:{feed_id}</id>',
             f'<title>{esc(title)}</title>',
             f'<updated>{now}</updated>',
             f'<author><name>{esc(title)}</name></author>']
    for r in rows:
        pub = r["published"] or now
        parts += ['<entry>',
                  f'<id>{esc(r["guid"])}</id>',
                  f'<title>{esc(r["title"])[:300]}</title>',
                  f'<link rel="alternate" href="{esc(r["link"])}"/>',
                  f'<published>{esc(pub)}</published>',
                  f'<updated>{esc(pub)}</updated>',
                  f'<author><name>{esc(r["author"])}</name></author>',
                  f'<summary>{esc((r["summary"] or "")[:500] or SCOPE)}</summary>',
                  '</entry>']
    parts.append('</feed>')
    return "\n".join(parts) + "\n"


def git_push(files):
    subprocess.run(["git", "add"] + files + ["last_mirror_receipt.json"], cwd=HERE, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
        return "no-change"
    subprocess.run(["git", "commit", "-m",
                    "builders mirror feeds " + datetime.now().strftime("%Y-%m-%d %H:%M")],
                   cwd=HERE, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=HERE, check=True, capture_output=True)
    return "pushed"


def main(proxy=None):
    proxy = proxy or (sys.argv[1] if len(sys.argv) > 1
                      else os.environ.get("YT_RADAR_PROXY", "http://127.0.0.1:12334"))
    receipts, written = [], []
    for kind, filename, title in KINDS:
        try:
            rows = flatten(kind, fetch_upstream(kind, proxy))
            (HERE / filename).write_text(build_atom(rows, filename[:-4], title), encoding="utf-8")
            written.append(filename)
            receipts.append({"kind": kind, "status": "completed", "items": len(rows)})
        except Exception as exc:
            receipts.append({"kind": kind, "status": "failed", "message": str(exc)[:150]})
    receipt = {"ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "proxy": proxy, "mirrors": receipts,
               "failed": sum(1 for r in receipts if r["status"] == "failed")}
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        receipt["git"] = git_push(written) if written else "nothing-written"
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        receipt["git"] = "failed: " + err[-200:]
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 1 if receipt["failed"] == len(receipts) else 0


if __name__ == "__main__":
    sys.exit(main())
