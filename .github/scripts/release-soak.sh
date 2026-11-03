#!/usr/bin/env bash
# Sets the `Release Soak` status, a required check on main. Automerged
# third-party releases wait three days, the time a hijacked release (LiteLLM
# 1.107.0, tj-actions) usually takes to be caught and pulled. A diff cannot show
# this: such a PR is one clean-looking tag line. Renovate's minimumReleaseAge
# cannot do it either, because its docker datasource has no release dates
# outside Docker Hub.
#
# The clock is the age of the PR's head commit, so it covers one exact release.
# Third-party PRs stay frozen on that release (rebaseWhen: never in
# packageRules.json5), otherwise every new release or main commit restarts it.
# A frozen PR cannot rebase itself, so this script adds the soak-rebase label
# (keepUpdatedLabel) when one conflicts or its image is withdrawn. That restarts
# the hold on the rebuilt commit.
#
# Every run checks every open PR, because a hold ends with no event of its own.
# One GraphQL query fetches what each PR needs and unchanged statuses are not
# posted again, so a run costs a few requests (GITHUB_TOKEN allows 1,000 an
# hour) and any PR event or push to main can trigger one.
#
# Env: REPO, DRY_RUN (prints only).
set -euo pipefail

readonly HOLD_SECONDS=$((3 * 24 * 3600))
readonly CONTEXT="Release Soak"
readonly RESCUE_LABEL="soak-rebase"
now=$(date +%s)

post() { # sha state description; reads evaluate's current_state/_description
  [[ $2 == "$current_state" && $3 == "$current_description" ]] && return
  echo "  $2: $3"
  [[ -n ${DRY_RUN:-} ]] && return
  gh api --silent -X POST "repos/$REPO/statuses/$1" \
    -f context="$CONTEXT" -f state="$2" -f description="$3"
}

add_rescue_label() { # number labels
  [[ $2 == *,$RESCUE_LABEL,* ]] && return
  echo "  asking Renovate to rebuild"
  [[ -n ${DRY_RUN:-} ]] && return
  gh api --silent -X POST "repos/$REPO/issues/$1/labels" -f "labels[]=$RESCUE_LABEL"
}

remove_rescue_label() { # number
  echo "  rebuilt, removing $RESCUE_LABEL"
  [[ -n ${DRY_RUN:-} ]] && return
  gh api --silent -X DELETE "repos/$REPO/issues/$1/labels/$RESCUE_LABEL"
}

# Prints each image a PR pins, as repo@digest or repo:tag. Reads both
# `image: repo:tag@digest` and the app-template form, where the diff shows
# `repository:` as context above the changed `tag:`. Names without a registry
# host are skipped: a chart may keep the host in a separate `registry:` value,
# and a wrong guess here would report a healthy image as withdrawn.
image_refs() { # number
  gh api --paginate "repos/$REPO/pulls/$1/files" --jq '.[].patch // empty' | awk '
    /^@@/ { repo = "" }
    {
      changed = /^\+/
      sub(/^[-+ ]/, "")
      sub(/^[[:space:]]*-[[:space:]]+/, "")
      sub(/[[:space:]]#.*/, "")
      gsub(/["\047]/, "")
    }
    $1 == "repository:" { repo = $2 }
    changed && $1 == "image:" && NF > 1 { print $2 }
    changed && $1 == "tag:" && repo != "" { print repo ":" $2 }
  ' | sed -E 's#:[^/@]+@sha256:#@sha256:#' | grep -E '^[^/]+\.[^/]+/' | grep -v '[$]' | sort -u || true
}

# Prints the AI reviewer's verdict on one commit, from the marker in its comment.
review_result() { # number sha
  gh api --paginate "repos/$REPO/issues/$1/comments" --jq '
    .[] | select(.user.login == "github-actions[bot]") | .body
    | capture("<!-- ai-pr-reviewer:(?<marker>\\{.*?\\}) -->")? | .marker' |
    jq -r --arg sha "$2" 'select(.head_sha == $sha) | .review_result' | tail -1
}

# Returns 0 when published, 1 when withdrawn, 2 when the registry gave no answer.
image_state() { # ref
  local output
  if output=$(docker buildx imagetools inspect --raw "$1" 2>&1 >/dev/null); then
    return 0
  fi
  if grep -qiE 'not found|manifest unknown' <<<"$output"; then
    return 1
  fi
  echo "  $1: $output"
  return 2
}

# Prints one tab-separated line per open PR, in the field order evaluate takes.
# Authors and mergeable read as in the REST API: `name[bot]`, true/false/null.
open_prs() {
  gh api graphql --paginate -f owner="${REPO%/*}" -f name="${REPO#*/}" -f query='
    query($owner: String!, $name: String!, $endCursor: String) {
      repository(owner: $owner, name: $name) {
        pullRequests(states: OPEN, first: 100, after: $endCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            number title mergeable
            autoMergeRequest { enabledAt }
            author { __typename login }
            labels(first: 50) { nodes { name } }
            commits(last: 1) { nodes { commit {
              oid committedDate
              status { context(name: "Release Soak") { state description } }
            } } }
          }
        }
      }
    }' --jq '.data.repository.pullRequests.nodes[] | .commits.nodes[0].commit as $c | [
      .number,
      (if .author.__typename == "Bot" then .author.login + "[bot]" else .author.login // "ghost" end),
      $c.oid,
      $c.committedDate,
      ({MERGEABLE: "true", CONFLICTING: "false"}[.mergeable] // "null"),
      (.autoMergeRequest != null | tostring),
      ("," +([.labels.nodes[].name] | join(",")) + ","),
      ($c.status.context.state // "-" | ascii_downcase),
      ($c.status.context.description // "-"),
      .title
    ] | @tsv'
}

evaluate() { # number author sha committed mergeable auto_merge labels state description title
  local number=$1 author=$2 sha=$3 committed=$4 mergeable=$5 auto_merge=$6 labels=$7
  local current_state=$8 current_description=$9 title=${10}
  echo "#$number $title"

  if [[ $author != "mini-smurf-cluster[bot]" && $author != "renovate[bot]" ]]; then
    post "$sha" success "Not a Renovate update"
    return
  fi
  if [[ $labels == *,soak-skip,* ]]; then
    post "$sha" success "Hold skipped with the soak-skip label"
    return
  fi
  # Own images deploy on release. GitHub Actions already wait three days in
  # Renovate (they have release dates), and dashboards are JSON, not code.
  if [[ $title =~ ghcr\.io/ekenheim/ || $title == *"(github-action)"* || $title == *"(grafana-dashboards)"* ]]; then
    post "$sha" success "Exempt from the hold"
    return
  fi

  local ready
  ready=$(($(date -d "$committed" +%s) + HOLD_SECONDS))

  if [[ $mergeable == false ]]; then
    add_rescue_label "$number" "$labels"
  elif [[ $mergeable == true && $labels == *,$RESCUE_LABEL,* ]] && ((now < ready)); then
    remove_rescue_label "$number"
  fi

  if ((now < ready)); then
    post "$sha" pending "Held until $(date -u -d "@$ready" '+%a %d %b %H:%M UTC')"
    return
  fi

  local ref refs code unknown=""
  refs=$(image_refs "$number")
  for ref in $refs; do
    code=0
    image_state "$ref" || code=$?
    if ((code == 1)); then
      post "$sha" failure "Withdrawn upstream: ${ref:0:100}"
      add_rescue_label "$number" "$labels"
      return
    fi
    ((code == 0)) || unknown=$ref
  done
  if [[ -n $unknown ]]; then
    post "$sha" pending "Registry gave no answer for ${unknown:0:90}, retrying"
    return
  fi

  # A major that merges unattended also needs a clean AI review of this commit.
  # Only that verdict counts, so a model outage or an unfinished review leaves
  # the PR waiting. The reviewer can extend the hold, never shorten it: release
  # notes are written by whoever cut the release, hijacked or not.
  local major='^[a-z]+(\([^)]*\))?!:' verdict
  if [[ $title =~ $major && $auto_merge == true ]]; then
    verdict=$(review_result "$number" "$sha")
    if [[ $verdict != clean ]]; then
      post "$sha" pending "AI review: ${verdict:-none yet}. Label soak-skip to merge anyway"
      return
    fi
  fi
  post "$sha" success "Held three days"
}

# Read the list first: a failed query fails the run instead of sweeping nothing.
prs=$(open_prs)
while IFS=$'\t' read -r number author sha committed mergeable auto_merge labels state description title; do
  [[ -n $number ]] || continue
  evaluate "$number" "$author" "$sha" "$committed" "$mergeable" "$auto_merge" "$labels" "$state" "$description" "$title" </dev/null ||
    echo "::warning::Release Soak could not evaluate #$number"
done <<<"$prs"
