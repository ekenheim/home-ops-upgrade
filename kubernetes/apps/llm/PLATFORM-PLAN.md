# LLM platform plan (after joryirving/home-ops)

Decided 2026-09-28: rebuild the capabilities of Jory's LLM stack here, **without**
his RTX 3090 and without Neuralwatt. Power is out of scope. We depend on his
`misospace` projects (dispatch, courier, alert-triage, pr-reviewer-action) as-is.

Reference: <https://github.com/joryirving/home-ops/blob/main/docs/src/notes/llm-strategy.md>

## What we are building

| # | Capability | Pieces |
|---|---|---|
| 1 | Nothing stops when one plan caps out | LiteLLM pools with `order:` failover across ChatGPT, MiniMax, Kimi, Z.AI and worker4 |
| 2 | Alerts arrive explained | `alert-triage` fed by Alertmanager, posting to Discord |
| 3 | Every PR gets an AI review before auto-merge | `misospace/pr-reviewer-action` on the self-hosted runners |
| 4 | Agents can use the homelab's tools | more toolhive MCP servers (kubectl read-only, flux, talos, unifi, arr, seerr, grafana) |
| 5 | Better memory recall | a reranker next to the embeddings, wired into memini |
| 6 | Renovate runs on demand | self-hosted `renovate-operator` + `webhook` (replaces the Mend app) |
| 7 | Always-on agents with scheduled jobs | `openclaw` with Discord bots, next to hermes |
| 8 | Issues become PRs on their own | `courier` + `opencode` driven by dispatch (replaces foreman). **Retired 2026-10-20**, both removed |
| 9 | A coding assistant of our own | `opencode` web + Zed on an `auto` classifier alias |

## The one difference from Jory: no 3090

Jory's coding loop writes code with a dense 27B on the 3090. worker4 runs that
class of model at ~12 tok/s, so our **coder role runs on `implementation-pool`
(cloud first, worker4 last)**. Grooming, diff review, the `auto` classifier and
most scheduled jobs stay on Ornith (worker4), as they do on Jory's Strix box.

## Pools

| Alias | Order | Policy |
|---|---|---|
| `frontier-pool` | ChatGPT gpt-6-astra | Hard problems. No local floor: fails instead of silently getting dumber |
| `reasoning-pool` | ChatGPT gpt-5.6-terra → MiniMax-M3 → Qwen3.8-Flash-Next | Planning. MiniMax is the flat-plan floor, worker4 the free one |
| `implementation-pool` | MiniMax-M3.1-Flash → ChatGPT gpt-5.6-luna → Ornith | Carrying out a plan. MiniMax leads because it is the flat-rate capacity (Token Plan, 5-hour + weekly windows; see `apps/observability/exporters/minimax-quota-exporter/README.md`) |
| `local-pool` / `local-pool-chat` | Ornith (thinking / no thinking) → MiniMax-M3-chat | Cheap, private-first work with a cloud fallback for when worker4 is down |
| `auto` | classifier on `fast` → `local-pool-chat` / `MiniMax-M3-chat` / `reasoning-pool` | Interactive clients only. Never reaches `frontier-pool` |

The existing aliases (`self-hosted`, `fast`, `review`, `translate`,
`chatgpt`, `qwen3-embedding-0.6b`) are unchanged, and still never fall back to a
cloud plan. Only callers that pick a pool alias opt into the cloud.
`chatgpt` runs gpt-5.6-luna since 2026-10-11; the flagship gpt-6-astra is
reached only through `frontier-pool`, so everyday calls stop spending the
subscription at flagship rate.

**Private data never goes into the pools.** MiniMax, Kimi and Z.AI are
China-based. Email, finance and home data jobs are pinned to `self-hosted` or
`chatgpt`.

## Build order

Each phase is one PR. The repo auto-merges PRs, so each is validated with
flux-local before it is opened. A phase that needs a secret you have not created
yet ships with its `ks.yaml` commented out and is switched on in a follow-up.

| Phase | PR contents | Blocked on you |
|---|---|---|
| **0** | Pools, cloud models, `auto`, this doc. A zero-replica `LiteLLMProxy` so `LiteLLMVirtualKey` CRs can mint per-app keys into Secrets | Subscriptions + 3 API keys (cloud rungs stay dark until then) |
| **1a** | `alert-triage` + Alertmanager route | nothing; reuses the existing Discord webhook |
| **1b** | PR reviewer workflow on the self-hosted runners | nothing |
| **1c** | Reranker + memini wiring; extra toolhive MCP servers | UniFi, Grafana and arr API keys for their MCP servers |
| **2** | `renovate-operator` + `webhook` | a GitHub App, then turning off the Mend app |
| **3** | `openclaw` with one Discord bot and 3-4 read-only jobs | a Discord bot token |
| **4** | `courier` + `opencode` (coordinator) replacing foreman. Retired 2026-10-20 | none beyond existing `github_token` / `dispatch` items |
| **5** | `opencode` web + Zed on `auto` | nothing |

## What you need to do

1. **Buy the plans** and put the keys in the `litellm` Bitwarden item:
   - MiniMax **Coding Plan "Plus"** (~$20/mo or $200/yr) → `MINIMAX_API_KEY`
   - Kimi **Coding** (~$15/mo) → `KIMI_CODE_API_KEY`
   - Z.AI **GLM Coding Lite** (~$151/yr) → `ZAI_API_KEY`

   Buy MiniMax first. It leads `implementation-pool` and is the floor of
   `reasoning-pool`. Kimi and Z.AI can wait until usage shows they are needed;
   their pool rungs were removed on 2026-10-11 (an empty key still cost every
   reasoning-pool call a failed hop) and go back into the litellm ConfigMap
   from git history when a plan is bought.
2. **Phase 1c:** add `UNIFI_*`, `GRAFANA_TOKEN` and the arr API keys when you get there.
3. **Phase 2:** create a GitHub App for Renovate (steps in that PR).
4. **Phase 3:** create a Discord application + bot and add its token as
   `OPENCLAW_DISCORD_BOT_TOKEN` in a new `openclaw` Bitwarden item.

## Cost

| Item | Per month |
|---|---|
| ChatGPT Plus | already paid |
| MiniMax Plus | ~160 kr |
| Kimi Coding | ~140 kr |
| GLM Coding Lite | ~120 kr |
| **Total new** | **~420 kr** |

No hardware. Brave Search, ElevenLabs and Composio are optional and not planned.

## Known traps carried over from Jory's notes

- Kimi signals "cap reached" with **403**, not 429. LiteLLM retries but does not
  cool down on it; the `order:` fallback is what moves on.
- The `chatgpt/` provider still fails **non-streaming** `/chat/completions` on
  LiteLLM v1.103.0 (`optional_params.setdefault("stream", False)`), so in a pool
  a non-streaming caller skips the ChatGPT rung. Streaming callers use it.
- Failover is silent. Check the `x-litellm-model-id` response header or
  `litellm_deployment_successful_fallbacks_total` to see which rung answered.
- A pool's usable window is its **smallest** rung.
