"""Read a restored VolSync volume back and decide whether it is usable.

Runs in the check pod verify.sh creates, with the restored PVC mounted
read-only at /restore. Prints exactly one "VERIFY OK ..." line when every
check passed; verify.sh treats anything else, including a clean exit
without that line, as a failed restore.

SQLite files are copied to /work with their -wal before opening, so the
WAL is replayed the way the app would on start. Opening the restored copy
read-only would skip the WAL and can report a healthy database as corrupt.
"""

import json
import os
import shutil
import sqlite3
import sys
import time

RESTORE = "/restore"
WORK = "/work"


def walk(root):
    files = size = newest = 0
    unreadable = []
    for dirpath, dirnames, filenames in os.walk(
        root, onerror=lambda e: unreadable.append(e.filename)
    ):
        if dirpath == root and "lost+found" in dirnames:
            dirnames.remove("lost+found")
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError as e:
                unreadable.append(e.filename)
                continue
            if not os.path.isfile(os.path.join(dirpath, name)):
                continue
            files += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return files, size, newest, unreadable


def check_db(rel, index):
    src = os.path.join(RESTORE, rel)
    if not os.path.isfile(src) or os.path.getsize(src) == 0:
        return f"db {rel}: missing or empty"
    dst_dir = os.path.join(WORK, str(index))
    os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, os.path.basename(rel))
    shutil.copy2(src, dst)
    if os.path.isfile(src + "-wal"):
        shutil.copy2(src + "-wal", dst + "-wal")
    try:
        con = sqlite3.connect(dst)
        result = [row[0] for row in con.execute("PRAGMA integrity_check")]
        tables = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        con.close()
    except sqlite3.Error as e:
        return f"db {rel}: {e}"
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)
    if result != ["ok"]:
        return f"db {rel}: integrity_check: {'; '.join(result[:5])[:500]}"
    if tables == 0:
        return f"db {rel}: no tables"
    print(f"  db {rel}: ok, {tables} tables")
    return None


def main():
    app, backend = sys.argv[1], sys.argv[2]
    with open("/config/checks.json") as f:
        cfg = json.load(f).get(app)
    if cfg is None:
        print(f"VERIFY FAIL app={app} backend={backend}: no entry in checks.json")
        return 1

    files, size, newest, unreadable = walk(RESTORE)
    age_h = (time.time() - newest) / 3600 if newest else float("inf")
    print(f"  {files} files, {size} bytes, newest {age_h:.1f}h old")
    for path in unreadable[:10]:
        print(f"  warning: unreadable {path}")

    errors = []
    if files == 0:
        errors.append("restore is empty")
    for rel in cfg.get("required", []):
        path = os.path.join(RESTORE, rel)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            errors.append(f"required {rel}: missing or empty")
    dbs = cfg.get("dbs", [])
    for index, rel in enumerate(dbs):
        error = check_db(rel, index)
        if error:
            errors.append(error)
    max_age = cfg.get("max_age_hours")
    if max_age is not None and age_h > max_age:
        errors.append(f"newest file is {age_h:.1f}h old, limit {max_age}h")

    for error in errors:
        print(f"  FAIL {error}")
    if errors:
        print(f"VERIFY FAIL app={app} backend={backend} errors={len(errors)}")
        return 1
    print(
        f"VERIFY OK app={app} backend={backend} files={files} bytes={size} "
        f"dbs={len(dbs)} newest_age_h={age_h:.1f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
