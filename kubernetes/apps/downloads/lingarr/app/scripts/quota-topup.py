#!/usr/bin/env python3
"""Spend what is left of the MiniMax week on Lingarr before it resets.

MiniMax's weekly allowance does not roll over (Monday 00:00 UTC), and Lingarr's
own schedule (25 items at 01, 07, 13 and 19) is done within about an hour,
so most weeks end with a large unused remainder while thousands of items wait.
Inside the last BURN_HORIZON_HOURS before the weekly reset this keeps Lingarr's
queue fed: whenever fewer than QUEUE_LOW_WATER translations are pending or
running, it starts an extra AutomatedTranslationJob run (GET
/api/schedule/job/automation), which queues up to max_translations_per_run new
items. Lingarr dedupes against pending, running and completed requests itself.

Stops, in order:
- the quota exporter is stale or MiniMax answered with an error;
- the weekly window is at or below WEEKLY_STOP_PERCENT;
- the current 5-hour window is at or below WINDOW_STOP_PERCENT. Lingarr marks a
  request Failed after ~15 s of 429s and never retries it, so the 5-hour cap
  must not be hit; one run plus the queue below the low-water mark is ~35
  items, about 27 points of a 5-hour window and 3 of the week (2026-10-17);
- MiniMax throttled Lingarr recently (THROTTLE_BRAKE or more 429s on the
  lingarr key in 15 minutes): the queue drains instead of growing. Its short
  RPM/TPM throttle clears in about a minute, inside Lingarr's own retries
  (retry_delay 30 s, doubling, 5 tries), but more load would only make it worse;
- the 50 minutes before each subtitle-dedupe run, so the queue drains and the
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
WEEKLY_STOP = float(os.environ.get("WEEKLY_STOP_PERCENT", "13"))
WINDOW_STOP = float(os.environ.get("WINDOW_STOP_PERCENT", "35"))
LOW_WATER = int(os.environ.get("QUEUE_LOW_WATER", "10"))
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
    if weekly <= WEEKLY_STOP:
        print(f"skip: weekly at or below {WEEKLY_STOP:.0f}% ({state})", flush=True)
        return 0
    if window <= WINDOW_STOP:
        print(f"skip: 5h window at or below {WINDOW_STOP:.0f}% ({state})", flush=True)
        return 0
    throttled = prom('sum(increase(litellm_deployment_failure_responses_total{api_key_alias="lingarr", exception_status="429"}[15m]))') or 0
    if throttled >= THROTTLE_BRAKE:
        print(f"skip: {throttled:.0f} MiniMax 429s on lingarr in 15 min, letting the queue drain ({state})", flush=True)
        return 0
    if now.hour in DEDUPE_HOURS and now.minute < DEDUPE_PAUSE_MIN:
        print(f"skip: letting the queue drain for subtitle-dedupe at {now.hour:02d}:45 ({state})", flush=True)
        return 0

    active = len(get_json(f"{LINGARR}/api/translationrequest/active"))
    if active >= LOW_WATER:
        print(f"skip: {active} translation(s) pending or running ({state})", flush=True)
        return 0

    with urllib.request.urlopen(f"{LINGARR}/api/schedule/job/automation", timeout=30) as r:
        r.read()
    print(f"started an automation run: {active} active ({state})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
