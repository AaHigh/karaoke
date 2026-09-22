#!/usr/bin/env python3
"""Rewrite the hard-coded BUILTIN song list inside index.html.

Run this next to index.html when the YouTube playlist changes.
Does not need Grok. Needs network and python3.

  python3 update-builtin.py

Optional:

  ./update
  ./update PLxxxxxxxx
  ./update --add "Name" PLxxxxxxxx
  ./update --embed
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PLAYLIST = "PL0YLeM02ZJ57BZf6e5YUDMfEs73-t0LB7"
HOSTS = [
    "https://invidious.f5.si",
    "https://invidious.nerdvpn.de",
    "https://yt.cdaut.de",
    "https://inv.nadeko.net",
    "https://invidious.projectsegfau.lt",
]


ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
KEEP_BOTH_FILE = "keep-both.json"


def fmt_dur(sec) -> str:
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        return ""
    return f"{sec // 60}:{sec % 60:02d}"


def parse_seconds(text) -> int:
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text or "").strip()
    if not s:
        return 0
    parts = s.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def is_junk(vid: str, title: str, author: str, seconds: int) -> bool:
    title = (title or "").strip()
    author = (author or "").strip()
    if not vid:
        return True
    if not title:
        return True
    if title == vid:
        return True
    if ID_RE.match(title) and not author:
        return True
    if seconds <= 0 and (not author or title == vid):
        return True
    if seconds <= 0 and ID_RE.match(title):
        return True
    return False


def clean_song_bits(title: str) -> tuple[str, str]:
    raw = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(
        r"(?i)\s*[\(\[][^)\]]*(karaoke|instrumental|lyrics|official|version|hd)[^)\]]*[\)\]]",
        " ",
        raw,
    )
    t = re.sub(r"(?i)\s*[-–—|•·]+\s*(karaoke|instrumental).*$", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|•·")
    artist = ""
    song = t
    for sep in [" - ", " – ", " — ", " • ", " · ", " | "]:
        if sep in t:
            left, right = t.split(sep, 1)
            if len(left) <= 40:
                artist, song = left.strip(), right.strip()
            break
    return artist, song


def load_keep_both(here: Path) -> set[tuple[str, str]]:
    path = here / KEEP_BOTH_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    pairs = set()
    for row in data.get("pairs") or []:
        ids = row.get("ids") or []
        if len(ids) >= 2:
            a, b = sorted(ids[:2])
            pairs.add((a, b))
    return pairs


def report_duplicates(here: Path, songs: list[dict]) -> None:
    kept = load_keep_both(here)
    buckets = {}
    for song in songs:
        artist, name = clean_song_bits(song.get("t") or "")
        key = re.sub(r"[^a-z0-9]+", " ", (name or song.get("t") or "").lower()).strip()
        if len(key) < 4:
            continue
        buckets.setdefault(key, []).append(song)
    print("--- possible duplicate songs ---")
    found = 0
    for key, rows in buckets.items():
        if len(rows) < 2:
            continue
        found += 1
        secs = [parse_seconds(s.get("d")) for s in rows]
        close = max(secs) - min(secs) <= 20 if secs else False
        ids = [s["id"] for s in rows]
        pair = tuple(sorted(ids[:2]))
        flagged = "KEEP-BOTH" if pair in kept else ("similar length" if close else "check versions")
        print(f"* {key} [{flagged}]")
        for song, sec in zip(rows, secs):
            print(f"    {song['id']}  {song.get('d')}  {song.get('t')}")
        if pair not in kept:
            print(f"    to keep both: add {{\"ids\": {json.dumps(ids[:2])}, \"note\": \"keep both\"}} to {KEEP_BOTH_FILE}")
    if not found:
        print("none")
    print("---")


def fetch_page(host: str, plid: str, page: int) -> dict:
    url = f"{host}/api/v1/playlists/{plid}?page={page}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def fetch_playlist(plid: str) -> tuple[str, list[dict]]:
    last_err = "no host"
    for host in HOSTS:
        try:
            songs = []
            seen = set()
            title = plid
            for page in range(1, 11):
                data = fetch_page(host, plid, page)
                title = data.get("title") or title
                raw = data.get("videos") or []
                fresh = 0
                for item in raw:
                    vid = item.get("videoId")
                    if not vid or vid in seen:
                        continue
                    title_text = item.get("title") or ""
                    author = item.get("author") or ""
                    seconds = item.get("lengthSeconds") or 0
                    try:
                        seconds = int(seconds)
                    except (TypeError, ValueError):
                        seconds = 0
                    if is_junk(vid, title_text, author, seconds):
                        print(f"cull {vid} ({title_text or 'no title'})")
                        seen.add(vid)
                        continue
                    seen.add(vid)
                    songs.append(
                        {
                            "id": vid,
                            "t": title_text or vid,
                            "c": author,
                            "d": fmt_dur(seconds),
                            "g": (item.get("description") or "")[:800],
                        }
                    )
                    fresh += 1
                print(f"{host} page {page}: {len(raw)} rows, {fresh} new, {len(songs)} total")
                if not raw or fresh == 0:
                    break
            if songs:
                return title, songs
            last_err = f"{host} empty"
        except Exception as exc:
            last_err = f"{host} {exc}"
            print(last_err)
    raise SystemExit(f"playlist fetch failed: {last_err}")


def current_embed_map(html: str) -> dict[str, int]:
    match = re.search(r"const BUILTIN = (\[.*?\]);", html)
    if not match:
        return {}
    try:
        rows = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return {row["id"]: int(row.get("e", 1)) for row in rows if "id" in row}


def oembed_ok(vid: str) -> bool:
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        "https://www.youtube.com/watch?v=" + vid
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            json.loads(resp.read().decode())
        return True
    except Exception:
        return False


def patch_html(html: str, songs: list[dict]) -> str:
    blob = json.dumps(songs, ensure_ascii=False, separators=(",", ":"))
    if re.search(r"const BUILTIN = \[.*?\];", html):
        return re.sub(r"const BUILTIN = \[.*?\];", "const BUILTIN = " + blob + ";", html, count=1)
    if re.search(r"const SONGS = \[.*?\];", html):
        return re.sub(r"const SONGS = \[.*?\];", "const SONGS = " + blob + ";", html, count=1)
    raise SystemExit("index.html has no BUILTIN or SONGS array to replace")


def load_master(here: Path) -> dict:
    path = here / "playlists.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"home": DEFAULT_PLAYLIST, "playlists": []}


def save_master(here: Path, data: dict) -> None:
    (here / "playlists.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def patch_playlists_js(html: str, playlists: list) -> str:
    blob = json.dumps(playlists, ensure_ascii=False, separators=(",", ":"))
    if re.search(r"const PLAYLISTS = \[.*?\];", html):
        return re.sub(r"const PLAYLISTS = \[.*?\];", "const PLAYLISTS = " + blob + ";", html, count=1)
    return html.replace(
        "const DEFAULT_PLAYLIST =",
        "const PLAYLISTS = " + blob + ";\nconst DEFAULT_PLAYLIST =",
        1,
    )


def add_named_playlist(here: Path, html_path: Path, name: str, plid: str) -> None:
    master = load_master(here)
    rows = master.setdefault("playlists", [])
    for row in rows:
        if row.get("id") == plid:
            row["name"] = name
            break
    else:
        rows.append({"id": plid, "name": name})
    save_master(here, master)
    html = html_path.read_text(encoding="utf-8")
    html_path.write_text(patch_playlists_js(html, rows), encoding="utf-8")
    print(f"added {name} ({plid}) to playlists.json and index.html")


def main() -> None:
    raw_args = sys.argv[1:]
    check_embed = "--embed" in raw_args
    args = [a for a in raw_args if a != "--embed"]
    here = Path(__file__).resolve().parent
    html_path = here / "index.html"
    if len(args) >= 3 and args[0] == "--add":
        if not html_path.exists():
            html_path = Path.cwd() / "index.html"
        add_named_playlist(here, html_path, args[1], args[2])
        return
    plid = args[0] if args else DEFAULT_PLAYLIST
    if not html_path.exists():
        html_path = Path.cwd() / "index.html"
    if not html_path.exists():
        raise SystemExit("index.html not found next to the script or in the current directory")

    html = html_path.read_text(encoding="utf-8")
    old_embed = current_embed_map(html)
    title, rows = fetch_playlist(plid)

    out = []
    for row in rows:
        if check_embed:
            flag = 1 if oembed_ok(row["id"]) else 0
            print(("HERE " if flag else "YT   ") + row["id"] + " " + row["t"][:50])
        else:
            flag = old_embed.get(row["id"], 1)
        out.append(
            {
                "id": row["id"],
                "t": row["t"],
                "c": row["c"],
                "d": row["d"],
                "e": flag,
                "g": row.get("g") or "",
            }
        )

    html_path.write_text(patch_html(html, out), encoding="utf-8")
    here_n = sum(s["e"] for s in out)
    print(f"wrote {len(out)} songs into {html_path}")
    print(f"playlist: {title} ({plid})")
    print(f"play-here flags kept or set: {here_n}")
    report_duplicates(here, out)
    print("open the new index.html. no Grok step.")


if __name__ == "__main__":
    main()
