#!/usr/bin/env python3
"""成章 YouTube 雷达 · Mac mini 采集器（方案 B：Mac mini 采集 → GitHub feed → 服务器拉取）

背景：服务器（火山引擎国内机）无 Google 系出口，YouTube 九个频道改由本机
经系统代理（默认 http://127.0.0.1:12334，可用环境变量 YT_RADAR_PROXY 覆盖，
传 "direct" 表示不走代理）用 yt-dlp 扁平发现（不下载视频），合并产出单个
Atom feed（youtube-radar.xml）推送到 GitHub 公开仓库；成章服务器每日 09:00
通过「海外订阅」自定义 RSS 条目拉取 raw 地址入库，按 remote_id 去重。

过滤规则与服务器端 youtube-public 保持一致：跳过 Shorts、过滤不足
min_duration_seconds 的视频（扁平模式下时长缺失的条目保守跳过）。
"""
import json
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES_FILE = HERE / "sources.json"
OUT_XML = HERE / "youtube-radar.xml"
RECEIPT = HERE / "last_run_receipt.json"

PER_SOURCE_LIMIT = 3    # 每频道最多发现条数（服务器直连链路为 1，这里放宽以加快补齐）
TOTAL_LIMIT = 30        # feed 总条目上限；服务器每轮取最新 5，按 remote_id 去重滚动补齐
DEFAULT_MIN_DURATION = 600


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def discover(source, proxy):
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(PER_SOURCE_LIMIT),
           "--skip-download", "--no-warnings", "--socket-timeout", "15",
           "--retries", "1", "--extractor-retries", "1", "--dump-json"]
    if proxy and proxy != "direct":
        cmd += ["--proxy", proxy]
    cmd.append(source["url"])
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1][:150]
                           if (out.stderr or out.stdout).strip() else "yt-dlp 失败")
    rows = []
    minimum = int(source.get("min_duration_seconds") or DEFAULT_MIN_DURATION)
    for line in out.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = str(entry.get("id") or "").strip()
        url = str(entry.get("webpage_url") or entry.get("url") or "")
        title = str(entry.get("title") or "").strip()
        if not vid or not title or "/shorts/" in url:
            continue
        duration = entry.get("duration")
        if minimum and duration is not None and int(duration) < minimum:
            continue
        if duration is None and minimum:
            continue  # 扁平模式拿不到时长时保守跳过，与服务器语义一致
        ud = str(entry.get("upload_date") or "")
        published = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}" if len(ud) == 8 else ""
        rows.append({"video_id": vid, "title": title, "published": published,
                     "link": f"https://www.youtube.com/watch?v={vid}",
                     "channel": source["name"], "duration": duration})
    return rows


def build_atom(items):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">',
             '<id>tag:chengzhang.amilakids.com,2026:youtube-radar-macmini</id>',
             '<title>YouTube 雷达 · Mac mini 采集</title>',
             f'<updated>{now}</updated>',
             '<author><name>YouTube 雷达 · Mac mini</name></author>']
    for it in items[:TOTAL_LIMIT]:
        pub = it["published"] or now[:10]
        parts += ['<entry>',
                  f'<id>youtube:{esc(it["video_id"])}</id>',
                  f'<yt:videoId>{esc(it["video_id"])}</yt:videoId>',
                  f'<title>{esc(it["title"])}</title>',
                  f'<link rel="alternate" href="{esc(it["link"])}"/>',
                  f'<published>{esc(pub)}T00:00:00+00:00</published>',
                  f'<updated>{esc(pub)}T00:00:00+00:00</updated>',
                  f'<author><name>{esc(it["channel"])}</name></author>',
                  f'<summary>{esc(it["channel"])} 频道公开视频发现记录（Mac mini 链路，≥600s 已过滤）；未读取字幕或正文。</summary>',
                  '</entry>']
    parts.append('</feed>')
    return "\n".join(parts) + "\n"


def git_push():
    subprocess.run(["git", "add", "youtube-radar.xml", "last_run_receipt.json"],
                   cwd=HERE, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
        return "no-change"
    subprocess.run(["git", "commit", "-m",
                    "youtube radar feed " + datetime.now().strftime("%Y-%m-%d %H:%M")],
                   cwd=HERE, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=HERE, check=True, capture_output=True)
    return "pushed"


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YT_RADAR_PROXY", "http://127.0.0.1:12334")
    sources = json.loads(SOURCES_FILE.read_text())["sources"]

    items, receipts = [], []
    for s in sources:
        if s.get("status") != "active":
            continue
        try:
            rows = discover(s, proxy)
            items.extend(rows)
            receipts.append({"name": s["name"], "status": "completed", "read": len(rows)})
        except Exception as exc:
            receipts.append({"name": s["name"], "status": "failed", "message": str(exc)[:150]})

    # 每来源内部已按最新在前；跨来源按发布时间倒序（无日期的排最后）
    items.sort(key=lambda x: x["published"], reverse=True)
    OUT_XML.write_text(build_atom(items), encoding="utf-8")
    receipt = {"ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "proxy": proxy, "channels": receipts,
               "total_items": len(items[:TOTAL_LIMIT]),
               "failed": sum(1 for r in receipts if r["status"] == "failed")}
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        receipt["git"] = git_push()
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        receipt["git"] = "failed: " + err[-200:]
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 1 if receipt["failed"] == len(receipts) else 0


if __name__ == "__main__":
    sys.exit(main())
