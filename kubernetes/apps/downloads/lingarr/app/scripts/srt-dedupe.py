#!/usr/bin/env python3
"""Collapse frame-split SRT cues back into one cue per on-screen line.

Converting ASS subtitles to SRT turns every frame of an animated sign into its
own cue (~40 ms each, often one copy per layered style), so a sign shown for
4 s becomes hundreds of identical cues. One-Punch Man S02E01 had 24,359 cues
(19,354 of them "HEARTTHROB HOTTIE SISTERS") against ~500 for a normal
episode, and Lingarr sent every one of them to MiniMax. This merges cues with
the same text whose time ranges touch (within GAP_MS) into one cue spanning
the run. Distinct text is never dropped.

A file is rewritten only when it is dense (MIN_RATE cues per minute; normal
subtitles are ~15, dense anime ~60) AND merging removes at least MIN_REDUCTION
of its cues, so a cleaned file is left alone on the next run. Originals are
copied once to --backup-dir (mirroring their path) before the first rewrite.
Nothing is written while Lingarr has a pending or running translation: it
resumes by line position, so changing a file under it would scramble the job.

Dry run by default; --write rewrites files.
"""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.request

GAP_MS = 120
TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
TAG = re.compile(r"<[^>]*>|\{[^}]*\}")


def ms(h, m, s, f):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(f.ljust(3, "0")[:3])


def fmt(t):
    h, t = divmod(t, 3600000)
    m, t = divmod(t, 60000)
    s, f = divmod(t, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"


def parse(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines[:2]):
            m = TS.search(line)
            if m:
                body = "\n".join(lines[i + 1:])
                if body.strip():
                    g = m.groups()
                    cues.append([ms(*g[:4]), ms(*g[4:]), body])
                break
    return cues


def key(body):
    return " ".join(TAG.sub("", body).split()).casefold()


def dedupe(cues):
    runs, open_runs = [], {}
    for start, end, body in sorted(cues, key=lambda c: (c[0], c[1])):
        k = key(body)
        run = open_runs.get(k)
        if run is not None and start <= run[1] + GAP_MS:
            run[1] = max(run[1], end)
            continue
        run = [start, end, body]
        open_runs[k] = run
        runs.append(run)
    return sorted(runs, key=lambda c: (c[0], c[1]))


def render(cues):
    return "".join(f"{i}\n{fmt(s)} --> {fmt(e)}\n{b}\n\n" for i, (s, e, b) in enumerate(cues, 1))


def plan(path, min_rate, min_reduction):
    """Return (rendered, before, after, rate) when path should be rewritten, else None."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if raw.count(b"-->") >= 1000:
            print(f"skip (not UTF-8): {path}", flush=True)
        return None
    cues = parse(text)
    if len(cues) < 200:
        return None
    rate = len(cues) / max(max(c[1] for c in cues) / 60000, 1)
    if rate < min_rate:
        return None
    merged = dedupe(cues)
    if 1 - len(merged) / len(cues) < min_reduction:
        return None
    rendered = render(merged)
    check = parse(rendered)
    if len(check) != len(merged) or {key(c[2]) for c in check} != {key(c[2]) for c in cues}:
        print(f"skip (round-trip check failed): {path}", flush=True)
        return None
    return rendered, len(cues), len(merged), rate


def lingarr_busy(url):
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/translationrequest/active", timeout=30) as r:
        return len(json.load(r))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="directories to scan for *.srt")
    ap.add_argument("--write", action="store_true", help="rewrite files (default: dry run)")
    ap.add_argument("--backup-dir", required=True, help="originals are copied here, mirroring their path")
    ap.add_argument("--lingarr-url", required=True, help="refuse to write while Lingarr has active translations")
    ap.add_argument("--min-rate", type=float, default=40, help="cues per minute (default 40)")
    ap.add_argument("--min-reduction", type=float, default=0.25, help="fraction of cues merged away (default 0.25)")
    args = ap.parse_args()

    todo = []
    for root in args.roots:
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name.lower().endswith(".srt"):
                    path = os.path.join(dirpath, name)
                    try:
                        p = plan(path, args.min_rate, args.min_reduction)
                    except OSError as e:
                        print(f"skip ({e}): {path}", flush=True)
                        continue
                    if p:
                        todo.append((path, *p))
                        print(f"{p[1]:6d} -> {p[2]:5d} cues ({p[3]:5.0f}/min)  {path}", flush=True)

    print(f"{len(todo)} file(s) to collapse", flush=True)
    if not todo or not args.write:
        return 0

    busy = lingarr_busy(args.lingarr_url)
    if busy:
        print(f"Lingarr has {busy} active translation(s); not writing, the next run retries", flush=True)
        return 0

    for path, rendered, *_ in todo:
        backup = os.path.join(args.backup_dir, os.path.relpath(path, "/"))
        if not os.path.exists(backup):
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(path, backup)
        tmp = path + ".dedupe-tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
        os.replace(tmp, path)
    print(f"collapsed {len(todo)} file(s); originals in {args.backup_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
