# MiniMax quota exporter, and how the MiniMax plan is run

The cluster's cloud LLM floor is one MiniMax **Token Plan** key (`MINIMAX_API_KEY`
on the `litellm` Bitwarden item). Every MiniMax alias in LiteLLM spends from it:
`translate` (Lingarr), `MiniMax-M3` / `-chat` / `-messages` (atlas, openclaw,
pr-review), `hindsight-consolidation`, and the cloud rungs of the pools. This
exporter makes the plan's own quota gauge visible in Prometheus; the alert rules
next to it turn "the plan ran out on Thursday" into a page on Tuesday.

## What MiniMax actually sells (researched 2026-10-12)

- **Token Plan, not prompts.** The 2025 Coding Plan (100 / 300 / 1,000 prompts
  per 5 hours) is gone. Tiers are Plus $22, Max $55, Ultra $132 a month; ours is
  Plus. Consumption is a weighted "resource" figure that MiniMax does not
  publish a formula for; it depends on model, context length, task complexity and
  thinking depth. Calls and raw tokens are both only proxies.
- **Two windows, clock-aligned, independent.** A 5-hour window with boundaries
  at 00, 05, 10, 15 and 20 UTC, and a weekly window from Monday 00:00 UTC to
  Monday 00:00 UTC (both read from `token_plan/remains`). A 5-hour reset does not
  restore the week. Nothing rolls over.
- **Three failure signals.**
  | Signal | Meaning | Clears |
  |---|---|---|
  | 429, code **2056**, "Token Plan usage limit reached" | 5-hour window spent | next 5-hour boundary |
  | 402, code **2067**, "usage limit has been reached ... upgrade or buy credits" | weekly allowance spent | Monday 00:00 UTC |
  | 429 without a plan message, or code 1002 / 1039 / 2045 | short-term RPM/TPM throttle, tightened at peak hours | about a minute |
- **No rate-limit headers.** Responses carry no `X-RateLimit-*` or `Retry-After`.
  The only view of where you stand is the remains endpoint (below) or the
  console at <https://platform.minimax.io/console/usage>.
- **Overflow.** Credit packs ($5 / $25 / $100, 1,000 credits per dollar, valid a
  year) top up automatically at pay-as-you-go list price once the plan is spent;
  or swap the subscription key for a pay-as-you-go key. MiniMax says the plan is
  for "individual, interactive developer use" and recommends pay-as-you-go for
  production.
- **Cache reads are suspect.** On the Anthropic-compatible endpoint long agent
  sessions re-send their cached prefix every turn and the quota drains far faster
  than new tokens explain (MiniMax-AI/MiniMax-M3#25, unanswered). openclaw's
  `MiniMax-M3-messages` traffic is 96% cache-read input.

## The gauge

```
GET https://api.minimax.io/v1/token_plan/remains
Authorization: Bearer <plan key>
```

(`coding_plan/remains` and the `www.minimax.io` host return the same body.)
Percentages are **remaining**. The exporter publishes, per `model` bucket
(`general` is the one every LLM call draws from; `video` is separate):

| Metric | Meaning |
|---|---|
| `minimax_token_plan_interval_remaining_percent` | 5-hour window left |
| `minimax_token_plan_weekly_remaining_percent` | weekly window left |
| `minimax_token_plan_interval_end_ms` / `_weekly_end_ms` | when each window resets (epoch ms) |
| `minimax_token_plan_interval_remains_ms` / `_weekly_remains_ms` | time left in each window |
| `minimax_token_plan_*_status` | MiniMax's status enum (1 observed on an active bucket, 3 on the unused video bucket) |
| `minimax_token_plan_api_status_code` | `base_resp.status_code`, 0 on success |

Scraped every 5 minutes. `up{job=~".*minimax-quota-exporter.*"}` is 0 when
MiniMax does not answer (missing key, outage), which is its own alert.

## Alerts (prometheusrule.yaml)

| Alert | Fires when | Severity |
|---|---|---|
| MiniMaxQuotaExporterDown | no successful probe for 15m | warning |
| MiniMaxWeeklyQuotaExhausted | weekly at 0% | critical |
| MiniMaxWeeklyQuotaLow | weekly below 25% | critical |
| MiniMaxWeeklyQuotaAheadOfPace | weekly below 50% on Monday or Tuesday (UTC) | warning |
| MiniMaxWeeklyQuotaBurst | more than 10 points of the week gone in one hour | warning |
| MiniMaxWindowQuotaLow | 5-hour window below 20% | warning |

`LiteLLMAuthOrQuotaFailures` (llm/litellm) stays as the "it already happened"
signal: it counts the 402s and 429s callers received.

## The process

1. **Measure the quota, not our token counts.** This exporter. LiteLLM's
   `litellm_*_tokens_metric_total` by `api_key_alias` says who spent, the
   remains gauge says how much of the plan it cost. Correlate the two for a
   few weeks before trusting any tokens-to-percent conversion.
2. **Alert on the quota, with teeth.** The rules above. Weekly below 25% is a
   page, not a dashboard colour.
3. **Give the week a budget and divide it.** Decide the priority order once.
   As of 2026-10-12: Lingarr and alert-triage are small and guaranteed; atlas
   and pr-review are the middle; openclaw and hindsight consolidation take what
   is left and are the first to be cut.
4. **Enforce it in LiteLLM with RPM, not TPM.** `model_rpm_limit` per key per
   MiniMax alias. A TPM cap is checked against input + `max_tokens` *before*
   the call: the 30k TPM cap on the hindsight key refused every retain
   (64k `max_tokens`) and most consolidation calls from 2026-10-08 with
   "Remaining: 30000" and nothing used. Hand-made keys (hindsight, memini,
   lingarr) take this through the admin API, operator keys through
   `rpmLimit` in `llm/litellm/keys/`:
   ```bash
   curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
     -X POST http://litellm.llm:4000/key/update \
     -d '{"key":"<key hash>","metadata":{"model_rpm_limit":{"MiniMax-M3":10,"hindsight-consolidation":10}}}'
   ```
   (`metadata` replaces the whole map: drop `model_tpm_limit` in the same call.)
5. **Trip the big consumers when the weekly gauge drops.** When
   MiniMaxWeeklyQuotaLow fires, set openclaw's and hindsight's MiniMax RPM to 0
   with the same call and restore them after the Monday reset. Automating this
   (a CronJob reading the gauge) is the follow-up once the numbers are trusted.
6. **Stop paying for cache reads on the Anthropic endpoint.** Until
   MiniMax-M3#25 is resolved, keep openclaw sessions short and thinking low, or
   point it at the OpenAI-compatible `MiniMax-M3-chat`. Hindsight consolidation
   belongs on the 5090 rung first, MiniMax second.
7. **Handle the three errors differently at the callers.** 2056: back off to
   the next 5-hour boundary, never retry in a loop (Lingarr's retries burned
   the window faster). 2067: stop using MiniMax for the rest of the week.
   Anything else 429: short exponential backoff. LiteLLM cools the deployment
   down on 429; `translate` has no fallback on purpose.
8. **Schedule batch work into quiet windows.** Start batch jobs just after a
   5-hour boundary and size them to finish inside it. Lingarr runs 25 episodes
   at 01, 07, 13 and 19 local (its own settings, see
   `apps/downloads/lingarr`). In the last 48 h before the weekly reset its
   `quota-topup` CronJob keeps Lingarr's queue at up to 100 items, sized so
   queued work fits above 10% of both the week and the 5-hour window (measured
   0.13 window and 0.02 weekly points per item, budgeted at twice that), and
   pauses on recent 429s.
9. **Decide the overflow policy once.** Either a $25 credit pack as a floor so a
   spent week degrades to pay-as-you-go, or accept that the plan runs dry and
   step 5 is the behaviour. It was left to chance the week of 2026-10-05.
10. **Review on Mondays.** Compare the remains history with LiteLLM's per-key
    tokens and spend, then set the RPM limits from data and retire the
    "300 prompts / 5 h" folklore wherever it still appears.

## Calibration so far (one day of data, treat as rough)

Week of 2026-10-12, 18 hours in: 14% of the week gone, 155M raw tokens through
LiteLLM, 145M of them openclaw's (96% cache-read). The week before, ~275M mostly
uncached tokens (hindsight consolidation 204M) spent 100% by Thursday. So cached
input is cheap, uncached input and output are not, and the weekly budget is
somewhere around 200M uncached-equivalent tokens on Plus. Lingarr's 290
output-heavy requests in 35 minutes hit 2056 on the night of 2026-10-11 while a
comparable volume used 13% of a window the next afternoon; whether that was the
window or the short-term throttle is not settled.
