#!/usr/bin/env python3
"""Build a filtered RSS + JSON Feed of fully remote, US-eligible senior platform roles.

Sources: We Work Remotely (RSS), Remote OK (JSON API), Himalayas (JSON API),
HN "Who is hiring?" (Algolia API). Stdlib only (Python 3.11+).

Usage:  python build_feed.py [--config config.toml] [--out public] [--explain]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

UTC = timezone.utc


# --------------------------------------------------------------------------- model
@dataclass
class Job:
    source: str
    id: str
    title: str
    company: str
    url: str
    published: datetime
    location: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def guid(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def salary_str(self) -> str:
        if self.salary_min and self.salary_max:
            return f"${self.salary_min:,}–${self.salary_max:,}"
        if self.salary_max or self.salary_min:
            return f"${(self.salary_max or self.salary_min):,}"
        return ""


# --------------------------------------------------------------------------- helpers
_BLOCK_END = re.compile(r"<(br\s*/?|p\b|/p|/li|/h\d|/div|/tr)[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def to_text(s: str | None) -> str:
    if not s:
        return ""
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def fetch(url: str, ua: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def from_rfc822(s: str) -> datetime:
    try:
        return parsedate_to_datetime(s).astimezone(UTC)
    except Exception:
        return datetime.now(UTC)


def from_epoch(v) -> datetime:
    try:
        return datetime.fromtimestamp(int(v), UTC)
    except Exception:
        return datetime.now(UTC)


def as_int(v) -> int | None:
    try:
        v = int(float(v))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- sources
def src_weworkremotely(cfg: dict, ua: str) -> list[Job]:
    jobs = []
    for cat in cfg["categories"]:
        root = ET.fromstring(fetch(f"https://weworkremotely.com/categories/{cat}.rss", ua, "application/rss+xml"))
        for it in root.iter("item"):
            g = lambda tag: (it.findtext(tag) or "").strip()  # noqa: E731
            company, sep, title = g("title").partition(": ")
            if not sep:
                company, title = "", g("title")
            desc = to_text(g("description"))
            hq = re.search(r"Headquarters:\s*([^\n]+)", desc)
            loc = " | ".join(x for x in (g("region"), g("country"), g("state"), hq.group(1) if hq else "") if x)
            jobs.append(Job(
                source="weworkremotely", id=g("guid") or g("link"), title=title, company=company,
                url=g("link"), published=from_rfc822(g("pubDate")), location=loc,
                description=desc, tags=[t for t in (g("category"),) if t],
            ))
        time.sleep(1)
    return jobs


def src_remoteok(cfg: dict, ua: str) -> list[Job]:
    jobs = []
    for d in json.loads(fetch("https://remoteok.com/api", ua)):
        if not isinstance(d, dict) or not d.get("position"):
            continue  # first element is the legal notice
        jobs.append(Job(
            source="remoteok", id=str(d.get("id") or d.get("slug")), title=d.get("position", ""),
            company=d.get("company", ""), url=d.get("url") or d.get("apply_url") or "",
            published=from_epoch(d.get("epoch")), location=d.get("location") or "",
            salary_min=as_int(d.get("salary_min")), salary_max=as_int(d.get("salary_max")),
            description=to_text(d.get("description")), tags=list(d.get("tags") or []),
        ))
    return jobs


def src_himalayas(cfg: dict, ua: str) -> list[Job]:
    jobs, cursor = [], None
    for _ in range(int(cfg.get("pages", 10))):
        url = "https://himalayas.app/jobs/api?limit=20"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        data = json.loads(fetch(url, ua))
        for j in data.get("jobs", []):
            usd_annual = (j.get("currency") in (None, "USD")) and (j.get("salaryPeriod") in (None, "annual"))
            jobs.append(Job(
                source="himalayas", id=j.get("guid") or j.get("applicationLink"), title=j.get("title", ""),
                company=j.get("companyName", ""), url=j.get("applicationLink") or j.get("guid") or "",
                published=from_epoch(j.get("pubDate")),
                location=", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                salary_min=as_int(j.get("minSalary")) if usd_annual else None,
                salary_max=as_int(j.get("maxSalary")) if usd_annual else None,
                description=to_text(j.get("description")), tags=list(j.get("categories") or []),
            ))
        cursor = data.get("nextCursor")
        if not cursor:
            break
        time.sleep(1)
    return jobs


def src_hn_whos_hiring(cfg: dict, ua: str) -> list[Job]:
    q = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10"
    hits = json.loads(fetch(q, ua)).get("hits", [])
    story = next((h for h in hits if h.get("title", "").lower().startswith("ask hn: who is hiring")), None)
    if not story:
        return []
    item = json.loads(fetch(f"https://hn.algolia.com/api/v1/items/{story['objectID']}", ua))
    jobs = []
    for c in item.get("children", []):
        body = to_text(c.get("text"))
        if not body:
            continue
        first = body.split("\n", 1)[0][:240]
        company = first.split("|", 1)[0].strip() or c.get("author", "")
        jobs.append(Job(
            source="hn", id=str(c["id"]), title=first, company=company,
            url=f"https://news.ycombinator.com/item?id={c['id']}",
            published=from_epoch(c.get("created_at_i")), location=first, description=body,
        ))
    return jobs


SOURCES = {
    "weworkremotely": src_weworkremotely,
    "remoteok": src_remoteok,
    "himalayas": src_himalayas,
    "hn_whos_hiring": src_hn_whos_hiring,
}


# --------------------------------------------------------------------------- filtering
class Rules:
    def __init__(self, f: dict, s: dict):
        c = lambda p: re.compile(p, re.I | re.M)  # noqa: E731
        self.title_include = c(f["title_include"])
        self.title_exclude = c(f["title_exclude"])
        self.require_seniority = f.get("require_seniority", True)
        self.seniority = c(f["seniority"])
        self.reject = [c(p) for p in f["reject_phrases"]]
        self.us_loc = c(f["us_ok_location"])
        self.us_body = c(f["us_ok_body"])
        self.non_us = c(f["non_us_location"])
        self.anywhere = c(f["anywhere_location"])
        self.min_salary = int(f.get("min_salary_usd", 0))
        self.kw = [c(k) for k in s.get("keywords", [])]
        self.kw_points = int(s.get("keyword_points", 2))
        self.title_bonus = c(s["title_bonus"]) if s.get("title_bonus") else None
        self.title_bonus_points = int(s.get("title_bonus_points", 0))
        self.salary_target = int(s.get("salary_target_usd", 0))
        self.salary_points = int(s.get("salary_target_points", 0))
        self.min_score = int(s.get("min_score", 0))

    def evaluate(self, j: Job) -> str | None:
        """Return a rejection reason, or None if the job passes. Sets score/reasons."""
        title, loc, body = j.title, j.location, j.description
        if not self.title_include.search(title):
            return "title-not-platform"
        if self.title_exclude.search(title):
            return "title-excluded"
        if self.require_seniority and not self.seniority.search(title):
            return "not-senior"
        for rx in self.reject:
            m = rx.search(loc) or rx.search(body)
            if m:
                return f"reject:{m.group(0)[:40]}"
        us = bool(self.us_loc.search(loc) or self.us_body.search(body))
        if self.non_us.search(loc) and not us:
            return "non-us-location"
        if not us and not self.anywhere.search(loc):
            return "location-unclear"
        if j.salary_max and j.salary_max < self.min_salary:
            return f"salary<{self.min_salary}"

        hay = f"{title}\n{body}"
        hits = sorted({re.sub(r"\\b|\\|\?", "", rx.pattern) for rx in self.kw if rx.search(hay)})
        score = len(hits) * self.kw_points
        if self.title_bonus and self.title_bonus.search(title):
            score += self.title_bonus_points
        if self.salary_target and (j.salary_max or 0) >= self.salary_target:
            score += self.salary_points
            hits.append("salary≥target")
        j.score, j.reasons = score, hits
        if score < self.min_score:
            return f"score<{self.min_score}"
        return None


def dedupe(jobs: list[Job]) -> list[Job]:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    best: dict[str, Job] = {}
    for j in jobs:
        key = f"{norm(j.company)}|{norm(j.title)}"
        cur = best.get(key)
        if cur is None or (j.salary_max and not cur.salary_max) or j.score > cur.score:
            best[key] = j
    return list(best.values())


# --------------------------------------------------------------------------- output
def item_html(j: Job) -> str:
    meta = " · ".join(x for x in (html.escape(j.company), html.escape(j.location), j.salary_str, j.source) if x)
    why = ", ".join(html.escape(r) for r in j.reasons) or "—"
    excerpt = html.escape(j.description[:700]).replace("\n", "<br>")
    return f"<p><b>{meta}</b></p><p>Score {j.score} — {why}</p><p>{excerpt}…</p>"


def write_rss(jobs: list[Job], feed: dict, path: Path) -> None:
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    for tag, val in (("title", feed["title"]), ("link", feed["link"]), ("description", feed["description"]),
                     ("lastBuildDate", format_datetime(datetime.now(UTC))), ("ttl", "240")):
        ET.SubElement(ch, tag).text = val
    for j in jobs:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = f"[{j.score}] {j.title} — {j.company}" if j.source != "hn" else f"[{j.score}] {j.title}"
        ET.SubElement(it, "link").text = j.url
        ET.SubElement(it, "guid", isPermaLink="false").text = j.guid
        ET.SubElement(it, "pubDate").text = format_datetime(j.published)
        ET.SubElement(it, "category").text = j.source
        ET.SubElement(it, "description").text = item_html(j)
    ET.indent(rss)
    path.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8"))


def write_json_feed(jobs: list[Job], feed: dict, path: Path) -> None:
    doc = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": feed["title"],
        "home_page_url": feed["link"],
        "feed_url": feed["link"].rstrip("/") + "/feed.json",
        "items": [{
            "id": j.guid, "url": j.url, "title": j.title, "date_published": j.published.isoformat(),
            "content_html": item_html(j), "tags": [j.source, *j.reasons],
            "_job": {"company": j.company, "location": j.location, "salary_min": j.salary_min,
                     "salary_max": j.salary_max, "score": j.score, "source": j.source},
        } for j in jobs],
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def write_index(n: int, feed: dict, path: Path) -> None:
    path.write_text(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(feed['title'])}</title>"
        f"<h1>{html.escape(feed['title'])}</h1><p>{n} roles · built {datetime.now(UTC):%Y-%m-%d %H:%M} UTC</p>"
        '<p><a href="feed.xml">RSS</a> · <a href="feed.json">JSON Feed</a></p>', encoding="utf-8")


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=Path(__file__).with_name("config.toml"), type=Path)
    ap.add_argument("--out", default=Path("public"), type=Path)
    ap.add_argument("--explain", action="store_true", help="print why each job was kept/dropped")
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    feed, ua = cfg["feed"], cfg["feed"]["user_agent"]
    rules = Rules(cfg["filters"], cfg.get("scoring", {}))

    raw, ok_sources = [], 0
    for name, fn in SOURCES.items():
        scfg = cfg["sources"].get(name, {})
        if not scfg.get("enabled", False):
            continue
        try:
            got = fn(scfg, ua)
            ok_sources += 1
            print(f"[{name}] fetched {len(got)}", file=sys.stderr)
            raw.extend(got)
        except Exception as e:  # one bad source shouldn't kill the feed
            print(f"[{name}] FAILED: {e!r}", file=sys.stderr)
    if ok_sources == 0:
        print("All sources failed; not writing feed.", file=sys.stderr)
        return 1

    cutoff = datetime.now(UTC) - timedelta(days=int(feed.get("max_age_days", 21)))
    kept, drops = [], {}
    for j in raw:
        reason = "too-old" if j.published < cutoff else rules.evaluate(j)
        if reason:
            drops[reason.split(":")[0]] = drops.get(reason.split(":")[0], 0) + 1
            if args.explain:
                print(f"  DROP {reason:<22} {j.source:<15} {j.title[:70]}", file=sys.stderr)
        else:
            kept.append(j)
            if args.explain:
                print(f"  KEEP score={j.score:<3}          {j.source:<15} {j.title[:70]}", file=sys.stderr)

    jobs = sorted(dedupe(kept), key=lambda j: j.published, reverse=True)[: int(feed.get("max_items", 150))]
    print(f"kept {len(jobs)} of {len(raw)}; dropped {drops}", file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    write_rss(jobs, feed, args.out / "feed.xml")
    write_json_feed(jobs, feed, args.out / "feed.json")
    write_index(len(jobs), feed, args.out / "index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
