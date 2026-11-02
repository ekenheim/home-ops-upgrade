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
# Env: REPO, PR_NUMBER (empty sweeps every open PR), DRY_RUN (prints only).
set -euo pipefail

readonly HOLD_SECONDS=$((3 * 24 * 3600))
readonly CONTEXT="Release Soak"
readonly RESCUE_LABEL="soak-rebase"
now=$(date +%s)

post() { # sha state description
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

evaluate() { # number
  local number=$1 author sha mergeable labels title
  IFS=$'\t' read -r author sha mergeable labels title < <(
    gh api "repos/$REPO/pulls/$number" --jq \
      '[.user.login, .head.sha, (.mergeable | tostring), ("," + ([.labels[].name] | join(",")) + ","), .title] | @tsv'
  )
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

  local committed ready
  committed=$(gh api "repos/$REPO/commits/$sha" --jq .commit.committer.date)
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
    post "$sha" pending "Registry gave no answer for ${unknown:0:90}, retrying hourly"
    return
  fi
  post "$sha" success "Held three days"
}

if [[ -n ${PR_NUMBER:-} ]]; then
  evaluate "$PR_NUMBER"
else
  for number in $(gh api --paginate "repos/$REPO/pulls?state=open&per_page=100" --jq '.[].number'); do
    evaluate "$number" || echo "::warning::Release Soak could not evaluate #$number"
  done
fi
