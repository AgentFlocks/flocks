#!/usr/bin/env bash
# Publish the checked-out source branch (flocksdev dev) to TARGET_REPOSITORY through one open sync PR into
# TARGET_BRANCH. While that PR is open its branch is only fast-forwarded; once it is merged or closed, the next run
# starts a new branch. Driven by .github/workflows/sync-dev-to-flocks.yml; tests/scripts/test_sync_dev_to_flocks.py
# runs it against local repos with fake gh and gitleaks.
set -euo pipefail

: "${SOURCE_BRANCH:?}"
: "${TARGET_REPOSITORY:?}"
: "${TARGET_BRANCH:?}"
: "${SYNC_BRANCH_PREFIX:?}"
: "${GITHUB_REPOSITORY:?}"

if [[ -z "${FLOCKS_SYNC_TOKEN:-}" ]]; then
  echo "::error title=Missing sync token::Repository secret FLOCKS_SYNC_TOKEN is not configured. It needs Contents, Pull requests and Workflows write access to ${TARGET_REPOSITORY}."
  exit 1
fi
# The prefix is spliced into a jq string literal below.
if [[ ! "${SYNC_BRANCH_PREFIX}" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "::error title=Invalid sync branch prefix::${SYNC_BRANCH_PREFIX}"
  exit 1
fi
if ! command -v gitleaks > /dev/null; then
  echo "::error title=gitleaks not found::The secret scan before publishing needs gitleaks on PATH."
  exit 1
fi

target_git_url="${TARGET_GIT_URL:-https://github.com/${TARGET_REPOSITORY}.git}"
temp_dir="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
source_sha="$(git rev-parse --verify "HEAD^{commit}")"
source_short="$(git rev-parse --short=12 "${source_sha}")"
source_label="${GITHUB_REPOSITORY} ${SOURCE_BRANCH}"
target_label="${TARGET_REPOSITORY} ${TARGET_BRANCH}"

write_summary() {
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] || return 0
  {
    echo "### Sync ${source_label} into ${target_label}"
    echo
    printf -- '- %s\n' "$@"
  } >> "${GITHUB_STEP_SUMMARY}"
}

askpass_script="$(mktemp "${temp_dir}/flocks-sync-askpass.XXXXXX")"
body_file="$(mktemp "${temp_dir}/flocks-sync-pr-body.XXXXXX")"
gitleaks_config="$(mktemp "${temp_dir}/flocks-sync-gitleaks.XXXXXX")"
gitleaks_log="$(mktemp "${temp_dir}/flocks-sync-gitleaks-log.XXXXXX")"
trap 'rm -f "${askpass_script}" "${body_file}" "${gitleaks_config}" "${gitleaks_log}"' EXIT
cat > "${askpass_script}" <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "$FLOCKS_SYNC_TOKEN" ;;
esac
EOF
chmod 700 "${askpass_script}"
# Default rules only, so a .gitleaks.toml on the source branch cannot narrow the scan; .gitleaksignore still applies.
printf '[extend]\nuseDefault = true\n' > "${gitleaks_config}"

export FLOCKS_SYNC_TOKEN
export GIT_ASKPASS="${askpass_script}"
export GIT_TERMINAL_PROMPT=0
export GH_TOKEN="${FLOCKS_SYNC_TOKEN}"

# A credential helper configured on the machine would answer before GIT_ASKPASS, so clear the list for the target.
target_git() {
  git -c credential.helper= "$@"
}

if git remote get-url sync-target > /dev/null 2>&1; then
  git remote set-url sync-target "${target_git_url}"
else
  git remote add sync-target "${target_git_url}"
fi

# Only PRs whose head branch lives in the target repository count: a fork can open a PR from a branch with the same name.
open_pr_filter="[.[] | select((.isCrossRepository | not) and (.headRefName | startswith(\"${SYNC_BRANCH_PREFIX}-\")))] | first // empty | [.headRefName, .url] | @tsv"
open_pr="$(
  gh pr list --repo "${TARGET_REPOSITORY}" --base "${TARGET_BRANCH}" --state open --limit 200 \
    --json headRefName,url,isCrossRepository --jq "${open_pr_filter}"
)"
pr_url=""
if [[ -n "${open_pr}" ]]; then
  IFS=$'\t' read -r sync_branch pr_url <<< "${open_pr}"
else
  sync_branch="${SYNC_BRANCH_PREFIX}-${source_short}"
fi

target_ref="refs/remotes/sync-target/${TARGET_BRANCH}"
sync_ref="refs/remotes/sync-target/${sync_branch}"
fetch_refspecs=("+refs/heads/${TARGET_BRANCH}:${target_ref}")
remote_sync_sha="$(
  target_git ls-remote --heads sync-target "refs/heads/${sync_branch}" |
    awk -v ref="refs/heads/${sync_branch}" '$2 == ref { print $1 }'
)"
if [[ -n "${remote_sync_sha}" ]]; then
  fetch_refspecs+=("+refs/heads/${sync_branch}:${sync_ref}")
fi
# One fetch, so the target branch and the sync branch come from the same moment.
target_git fetch --no-tags sync-target "${fetch_refspecs[@]}"
if [[ -n "${remote_sync_sha}" ]]; then
  remote_sync_sha="$(git rev-parse "${sync_ref}")"
fi

# Non-merge commits on the open PR that neither side has mean the source was rolled back or someone pushed to the PR.
# Checked before the early exit so a rollback to something the target already has is reported as well.
if [[ -n "${pr_url}" && -n "${remote_sync_sha}" ]]; then
  stray_count="$(git rev-list --count --no-merges "${remote_sync_sha}" "^${source_sha}" "^${target_ref}")"
  if [[ "${stray_count}" != 0 ]]; then
    echo "::error title=Sync PR has commits missing from ${source_label}::${pr_url} carries ${stray_count} commit(s) that neither ${source_label} nor ${target_label} has. If ${source_label} was rolled back, close that PR, delete ${sync_branch} in ${TARGET_REPOSITORY} and re-run this workflow. If they were pushed to the PR on purpose or it was updated with rebase, merge it first, then re-run."
    exit 1
  fi
fi

if git merge-base --is-ancestor "${source_sha}" "${target_ref}"; then
  echo "${target_label} already contains ${source_short}; nothing to sync."
  write_summary "Source: \`${source_short}\`" "Result: ${target_label} already contains it; nothing to sync"
  exit 0
fi

counts="$(git rev-list --left-right --count "${target_ref}...${source_sha}")"
read -r target_only source_only <<< "${counts}"
echo "${target_label} only: ${target_only}"
echo "${source_label} only: ${source_only}"

# Anything pushed from here on is public in the target repository, so scan the commits it does not have yet.
# --remerge-diff also shows what a merge commit itself adds, which plain `git log -p` leaves out.
scan_log_opts="--remerge-diff ${target_ref}..${source_sha}"
# gitleaks reports "no leaks" and exits 0 when its own `git log` fails, so run that git log first and treat any ERR
# line from gitleaks as a failed scan.
read -r -a scan_log_args <<< "${scan_log_opts}"
if ! git log -p -U0 "${scan_log_args[@]}" > /dev/null; then
  echo "::error title=Secret scan failed::git log ${scan_log_opts} failed, so gitleaks would have scanned nothing. Nothing was published."
  exit 1
fi
scan_status=0
gitleaks git --no-banner --redact --verbose --config "${gitleaks_config}" --log-opts="${scan_log_opts}" . \
  2> "${gitleaks_log}" || scan_status=$?
cat "${gitleaks_log}" >&2
# grep -c reads to the end, so sed never hits a closed pipe under pipefail.
scan_errors="$(sed -E 's/\x1b\[[0-9;]*m//g' "${gitleaks_log}" | grep -Ec '^[^ ]+ ERR ' || true)"
if [[ "${scan_status}" != 0 || "${scan_errors}" != 0 ]]; then
  echo "::error title=Secret scan failed::gitleaks flagged or could not scan the commits that would be published to ${TARGET_REPOSITORY} (log above, values redacted). Remove a real secret from ${source_label} and rotate it; for a false positive, add its Fingerprint to .gitleaksignore on ${SOURCE_BRANCH}. Then re-run this workflow."
  exit 1
fi

push_needed=true
if [[ -z "${remote_sync_sha}" ]]; then
  sync_action="created at ${source_short}"
elif [[ -z "${pr_url}" && "${remote_sync_sha}" != "${source_sha}" ]]; then
  # Left over from a closed PR; reusing it would publish whatever was pushed to it.
  echo "::error title=Sync branch has extra commits::${sync_branch} in ${TARGET_REPOSITORY} points at ${remote_sync_sha:0:12}, not ${source_short}, and has no open PR. Delete that branch in ${TARGET_REPOSITORY} and re-run this workflow."
  exit 1
elif git merge-base --is-ancestor "${source_sha}" "${remote_sync_sha}"; then
  push_needed=false
  sync_action="already contains ${source_short}"
elif git merge-base --is-ancestor "${remote_sync_sha}" "${source_sha}"; then
  sync_action="fast-forwarded to ${source_short}"
else
  # Only merges made on the PR (a conflict resolution, Update branch) get here. The branch cannot be fast-forwarded and
  # merging here is left to a person.
  echo "::error title=Sync PR has extra commits::${pr_url} has merge commits that ${source_label} lacks, so ${sync_branch} cannot be fast-forwarded to ${source_short}. Merge that PR and re-run this workflow; the re-run opens a new sync PR for the newer commits."
  exit 1
fi

if [[ "${push_needed}" == true ]]; then
  if ! target_git push sync-target "${source_sha}:refs/heads/${sync_branch}"; then
    echo "::error title=Push to ${TARGET_REPOSITORY} failed::Could not update ${sync_branch}. Check that FLOCKS_SYNC_TOKEN can write contents and workflows in ${TARGET_REPOSITORY}; if the branch moved during this run, re-run the workflow."
    exit 1
  fi
fi
echo "Sync branch ${sync_branch}: ${sync_action}."

if [[ -n "${pr_url}" ]]; then
  echo "Existing sync PR: ${pr_url}"
else
  run_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-}"
  cat > "${body_file}" <<EOF
Opened by the Sync dev to flocks workflow in ${GITHUB_REPOSITORY} at ${source_short}: ${run_url}

While this PR is open, every push to ${source_label} fast-forwards \`${sync_branch}\`. Once it is merged or closed, the next push opens a new sync PR from a new branch.

Merge it with **Create a merge commit**. A squash or rebase merge leaves the original commits out of \`${TARGET_BRANCH}\`, and the next sync PR would carry the same changes again.

Pushing to this branch here, Update branch included, pauses the sync until this PR is merged or closed, because the workflow only fast-forwards it.
EOF
  pr_url="$(
    gh pr create \
      --repo "${TARGET_REPOSITORY}" \
      --base "${TARGET_BRANCH}" \
      --head "${sync_branch}" \
      --title "chore: sync ${source_label} into ${TARGET_BRANCH}" \
      --body-file "${body_file}" |
      tail -n 1
  )"
  echo "Created sync PR: ${pr_url}"
fi

write_summary \
  "Source: \`${source_short}\`" \
  "${target_label} only: ${target_only}, ${source_label} only: ${source_only}" \
  "Sync branch \`${sync_branch}\`: ${sync_action}" \
  "PR: ${pr_url}"
