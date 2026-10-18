#!/usr/bin/env python3
"""Spend what is left of the MiniMax week on Lingarr before it resets.

MiniMax's weekly allowance does not roll over (Monday 00:00 UTC), and Lingarr's
own schedule (25 items at 01, 07, 13 and 19) is done within about an hour,
so most weeks end with a large unused remainder while thousands of items wait.
Inside the last BURN_HORIZON_HOURS before the weekly reset this keeps Lingarr's
queue deep: it sizes a target queue from what the quota can still pay for and
starts enough extra AutomatedTranslationJob runs (GET
/api/schedule/job/automation, RUN_SIZE items each, run one after another by
Lingarr) to fill it. Lingarr dedupes against pending, running and completed
requests itself.

The target is the smallest of
- QUEUE_TARGET: deep enough that 6 workers never run dry between ticks (they
  finish ~5 items a minute; 2026-10-17);
- the 5-hour window above WINDOW_FLOOR_PERCENT, at WINDOW_PTS_PER_ITEM. Lingarr
  marks a request Failed after its retries hit the 5-hour cap and never
  retries it, so queued plus running work must fit in what is left;
- the week above WEEKLY_FLOOR_PERCENT, at WEEKLY_PTS_PER_ITEM.
Measured 2026-10-17 (102 items in 20 minutes, mostly One Piece episodes of
~300 lines): 0.13 window and 0.02 weekly points per item; the defaults allow
twice that. As the window drains the target shrinks,
and refilling resumes after the next 5-hour boundary.

An empty queue starts a single probe run, not MAX_RUNS_PER_TICK: each run
scans the whole library (~16k items, 10-20 s), and once every item with an
English source has Swedish (2026-10-18) four of them every tick queue nothing.
When the probe finds work the queue is non-empty on the next tick and the
ramp continues from there.

Does nothing when the quota exporter is stale or MiniMax answered with an
error; when MiniMax throttled Lingarr recently (THROTTLE_BRAKE or more 429s on
the lingarr key in 15 minutes: the queue drains instead of growing); and for
the 50 minutes before each subtitle-dedupe run, so the queue drains and the
dedupe (which refuses to touch files while Lingarr is busy) gets to run.

Prints its decision on every run and exits 0 unless a call fails.
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request
import zoneinfo

PROM = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090")
LINGARR = os.environ.get("LINGARR_URL", "http://lingarr.downloads.svc.cluster.local:9876")
HORIZON_H = float(os.environ.get("BURN_HORIZON_HOURS", "48"))
WEEKLY_FLOOR = float(os.environ.get("WEEKLY_FLOOR_PERCENT", "10"))
WINDOW_FLOOR = float(os.environ.get("WINDOW_FLOOR_PERCENT", "10"))
WEEKLY_PTS = float(os.environ.get("WEEKLY_PTS_PER_ITEM", "0.04"))
WINDOW_PTS = float(os.environ.get("WINDOW_PTS_PER_ITEM", "0.25"))
QUEUE_TARGET = int(os.environ.get("QUEUE_TARGET", "100"))
RUN_SIZE = int(os.environ.get("RUN_SIZE", "25"))
MAX_RUNS = int(os.environ.get("MAX_RUNS_PER_TICK", "4"))
THROTTLE_BRAKE = float(os.environ.get("THROTTLE_BRAKE", "3"))
DEDUPE_HOURS = {int(h) for h in os.environ.get("DEDUPE_HOURS", "0,6,12,18").split(",")}
DEDUPE_PAUSE_MIN = 50
TZ = zoneinfo.ZoneInfo(os.environ.get("TZ", "Europe/Stockholm"))


def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def prom(expr):
    """Single value of an instant query, or None when it returns nothing."""
    data = get_json(f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": expr}))
    result = data["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def main():
    now = datetime.datetime.now(TZ)
    sel = '{model="general"}'
    age = prom(f"time() - max(timestamp(minimax_token_plan_weekly_remaining_percent{sel}))")
    status = prom("max(minimax_token_plan_api_status_code)")
    weekly = prom(f"min(minimax_token_plan_weekly_remaining_percent{sel})")
    window = prom(f"min(minimax_token_plan_interval_remaining_percent{sel})")
    reset_ms = prom(f"min(minimax_token_plan_weekly_remains_ms{sel})")
    if None in (age, status, weekly, window, reset_ms) or age > 900 or status != 0:
        print(f"skip: quota data unusable (age={age}s, status={status})", flush=True)
        return 0

    hours_left = reset_ms / 3_600_000
    state = f"weekly {weekly:.0f}% left, 5h window {window:.0f}% left, reset in {hours_left:.1f} h"
    if hours_left > HORIZON_H:
        print(f"skip: outside the last {HORIZON_H:.0f} h ({state})", flush=True)
        return 0
    throttled = prom('sum(increase(litellm_deployment_failure_responses_total{api_key_alias="lingarr", exception_status="429"}[15m]))') or 0
    if throttled >= THROTTLE_BRAKE:
        print(f"skip: {throttled:.0f} MiniMax 429s on lingarr in 15 min, letting the queue drain ({state})", flush=True)
        return 0
    if now.hour in DEDUPE_HOURS and now.minute < DEDUPE_PAUSE_MIN:
        print(f"skip: letting the queue drain for subtitle-dedupe at {now.hour:02d}:45 ({state})", flush=True)
        return 0

    window_items = max(0, (window - WINDOW_FLOOR) / WINDOW_PTS)
    weekly_items = max(0, (weekly - WEEKLY_FLOOR) / WEEKLY_PTS)
    target = int(min(QUEUE_TARGET, window_items, weekly_items))
    active = len(get_json(f"{LINGARR}/api/translationrequest/active"))
    # Whole runs that fit: a run queues up to RUN_SIZE items, so rounding up
    # could queue work the window cannot pay for.
    runs = min(MAX_RUNS, (target - active) // RUN_SIZE) if target > active else 0
    if active == 0:
        runs = min(runs, 1)
    plan = f"{active} active, target {target} (window allows {window_items:.0f}, week {weekly_items:.0f})"
    if runs <= 0:
        print(f"skip: {plan} ({state})", flush=True)
        return 0

    for _ in range(runs):
        with urllib.request.urlopen(f"{LINGARR}/api/schedule/job/automation", timeout=30) as r:
            r.read()
    print(f"started {runs} automation run(s): {plan} ({state})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
