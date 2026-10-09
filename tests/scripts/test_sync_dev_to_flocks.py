import base64
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sync-dev-to-flocks.yml"
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "sync-dev-to-flocks.sh"
WORKFLOW = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
WORKFLOW_ENV = WORKFLOW["env"]
SOURCE_BRANCH = WORKFLOW_ENV["SOURCE_BRANCH"]
TARGET_BRANCH = WORKFLOW_ENV["TARGET_BRANCH"]
PREFIX = WORKFLOW_ENV["SYNC_BRANCH_PREFIX"]
SYNC_TOKEN = "test-token"  # secret-guard: allow
PR_URL = "https://github.com/AgentFlocks/flocks/pull/{}"
PROXY_VARS = {name for base in ("http_proxy", "https_proxy", "all_proxy", "no_proxy") for name in (base, base.upper())}

# Answers `gh pr list` newest first, like gh, from a JSON file of PRs with the script's own --jq filter, and records
# `gh pr create` in that file.
# It logs whether gh got the sync token rather than the token itself.
FAKE_GH = r"""#!/usr/bin/env bash
if [ -n "${GH_TOKEN:-}" ] && [ "${GH_TOKEN}" = "${FLOCKS_SYNC_TOKEN:-}" ]; then
  auth=sync-token
else
  auth=other
fi
printf 'auth=%s %s\n' "$auth" "$*" >> "$FAKE_GH_LOG"
command="$1 $2"
shift 2
head="" jq_filter="" body_file=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --head) head="$2"; shift ;;
    --jq) jq_filter="$2"; shift ;;
    --body-file) body_file="$2"; shift ;;
  esac
  shift
done
case "$command" in
  "pr list")
    jq '[.[] | select(.state == "OPEN")] | reverse' "$FAKE_GH_PRS" | jq -r "$jq_filter"
    ;;
  "pr create")
    url="https://github.com/AgentFlocks/flocks/pull/$(( $(jq length "$FAKE_GH_PRS") + 1 ))"
    jq --arg head "$head" --arg url "$url" \
      '. + [{headRefName: $head, url: $url, isCrossRepository: false, state: "OPEN"}]' \
      "$FAKE_GH_PRS" > "$FAKE_GH_PRS.new"
    mv "$FAKE_GH_PRS.new" "$FAKE_GH_PRS"
    cp "$body_file" "$FAKE_GH_LOG.body"
    printf '%s\n' "$url"
    ;;
  *)
    echo "unexpected gh call: $command" >&2
    exit 1
    ;;
esac
"""

FAKE_GITLEAKS = r"""#!/usr/bin/env bash
printf '%s\n' "$@" "--end-of-call--" >> "$FAKE_GITLEAKS_LOG"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--config" ]; then
    cat "$2" > "$FAKE_GITLEAKS_LOG.config"
  fi
  shift
done
if [ -n "${FAKE_GITLEAKS_STDERR:-}" ]; then
  printf '%s\n' "$FAKE_GITLEAKS_STDERR" >&2
fi
exit "${FAKE_GITLEAKS_EXIT:-0}"
"""

needs_tools = pytest.mark.skipif(
    sys.platform == "win32" or any(shutil.which(tool) is None for tool in ("git", "bash", "jq")),
    reason="runs the sync script with git, bash, jq and fake gh / gitleaks executables",
)


class SyncRepos:
    """flocks.git plays AgentFlocks/flocks; flocksdev is the Actions checkout of the private repository."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.target = root / "flocks.git"
        self.source = root / "flocksdev"
        self.flocks_work = root / "flocks-work"
        self.runner_temp = root / "runner-temp"
        self.summary = root / "summary.md"
        self.gh_log = root / "gh.log"
        self.gh_prs = root / "prs.json"
        self.gitleaks_log = root / "gitleaks.log"
        self.global_config = root / "gitconfig"

        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, content in (("gh", FAKE_GH), ("gitleaks", FAKE_GITLEAKS)):
            tool = bin_dir / name
            tool.write_text(content, encoding="utf-8")
            tool.chmod(0o755)
        home = root / "home"
        home.mkdir()
        self.runner_temp.mkdir()
        self.global_config.write_text("", encoding="utf-8")
        self.gh_prs.write_text("[]", encoding="utf-8")

        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_") and key not in {"GH_TOKEN", "GITHUB_TOKEN", *PROXY_VARS}
        }
        self.env.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "GIT_CONFIG_GLOBAL": str(self.global_config),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Sync Test",
                "GIT_AUTHOR_EMAIL": "sync-test@example.com",
                "GIT_COMMITTER_NAME": "Sync Test",
                "GIT_COMMITTER_EMAIL": "sync-test@example.com",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            }
        )

        seed = root / "seed"
        self.git(root, "init", "-q", f"--initial-branch={TARGET_BRANCH}", str(seed))
        self._commit(seed, "README.md", "flocks\n", "chore: initial commit")
        self.git(root, "clone", "-q", "--bare", str(seed), str(self.target))
        # Refuse anything but fast-forwards on the target, whatever the pusher asks for.
        self.git(root, "--git-dir", str(self.target), "config", "receive.denyNonFastForwards", "true")
        self.git(root, "clone", "-q", "--branch", SOURCE_BRANCH, str(seed), str(self.source))
        self.git(root, "clone", "-q", str(self.target), str(self.flocks_work))

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"git {' '.join(args)} failed:\n{result.stderr}"
        return result.stdout.strip()

    def _commit(self, repo: Path, filename: str, content: str, message: str) -> str:
        (repo / filename).write_text(content, encoding="utf-8")
        self.git(repo, "add", filename)
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def commit_source(self, filename: str, content: str, message: str) -> str:
        return self._commit(self.source, filename, content, message)

    def _checkout_flocks(self, branch: str) -> None:
        self.git(self.flocks_work, "fetch", "-q", "origin")
        self.git(self.flocks_work, "checkout", "-q", "-B", branch, f"origin/{branch}")

    def _push_flocks(self, branch: str) -> str:
        self.git(self.flocks_work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return self.git(self.flocks_work, "rev-parse", "HEAD")

    def commit_flocks(self, branch: str, filename: str, content: str, message: str) -> str:
        self._checkout_flocks(branch)
        self._commit(self.flocks_work, filename, content, message)
        return self._push_flocks(branch)

    def update_pr_branch(self, branch: str) -> str:
        """What GitHub's Update branch button does: merge the base branch into the PR branch."""
        self._checkout_flocks(branch)
        self.git(self.flocks_work, "merge", "-q", "--no-ff", "-m", "Update branch", f"origin/{TARGET_BRANCH}")
        return self._push_flocks(branch)

    def merge_pr(self, url: str) -> None:
        prs = self.prs()
        pr = next(pr for pr in prs if pr["url"] == url)
        self._checkout_flocks(TARGET_BRANCH)
        self.git(self.flocks_work, "merge", "-q", "--no-ff", "-m", f"Merge {url}", f"origin/{pr['headRefName']}")
        self._push_flocks(TARGET_BRANCH)
        pr["state"] = "MERGED"
        self.gh_prs.write_text(json.dumps(prs), encoding="utf-8")

    def close_pr(self, url: str) -> None:
        prs = self.prs()
        next(pr for pr in prs if pr["url"] == url)["state"] = "CLOSED"
        self.gh_prs.write_text(json.dumps(prs), encoding="utf-8")

    def add_pr(self, head: str, url: str, *, cross_repository: bool) -> None:
        prs = self.prs()
        prs.append({"headRefName": head, "url": url, "isCrossRepository": cross_repository, "state": "OPEN"})
        self.gh_prs.write_text(json.dumps(prs), encoding="utf-8")

    def prs(self) -> list[dict]:
        return json.loads(self.gh_prs.read_text(encoding="utf-8"))

    def branch_sha(self, branch: str) -> str:
        return self.git(self.root, "--git-dir", str(self.target), "rev-parse", f"refs/heads/{branch}")

    def sync_branches(self) -> dict[str, str]:
        output = self.git(
            self.root,
            "--git-dir",
            str(self.target),
            "for-each-ref",
            "--format=%(refname:strip=2) %(objectname)",
            f"refs/heads/{PREFIX}-*",
        )
        return dict(line.split() for line in output.splitlines())

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    def gitleaks_calls(self) -> list[list[str]]:
        if not self.gitleaks_log.exists():
            return []
        calls: list[list[str]] = [[]]
        for line in self.gitleaks_log.read_text(encoding="utf-8").splitlines():
            if line == "--end-of-call--":
                calls.append([])
            else:
                calls[-1].append(line)
        return calls[:-1]

    def scanned_ranges(self) -> list[str]:
        return [
            arg.removeprefix("--log-opts=")
            for call in self.gitleaks_calls()
            for arg in call
            if arg.startswith("--log-opts=")
        ]

    def run_sync(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = {
            **self.env,
            **{key: str(value) for key, value in WORKFLOW_ENV.items()},
            "GITHUB_REPOSITORY": "AgentFlocks/flocksdev",
            "GITHUB_RUN_ID": "42",
            "FLOCKS_SYNC_TOKEN": SYNC_TOKEN,
            "TARGET_GIT_URL": str(self.target),
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_GH_PRS": str(self.gh_prs),
            "FAKE_GITLEAKS_LOG": str(self.gitleaks_log),
            **overrides,
        }
        return subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            cwd=self.source,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )


class _AuthRecorder(http.server.BaseHTTPRequestHandler):
    """Rejects every request with a Basic challenge and remembers the Authorization headers git sent."""

    def do_GET(self) -> None:
        self.server.seen_auth.append(self.headers.get("Authorization", ""))
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="flocks"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def repos(tmp_path: Path) -> SyncRepos:
    return SyncRepos(tmp_path)


def scan_range(source_sha: str) -> str:
    return f"--remerge-diff refs/remotes/sync-target/{TARGET_BRANCH}..{source_sha}"


def test_workflow_runs_only_in_flocksdev_on_dev_pushes() -> None:
    # PyYAML reads the bare `on:` key as boolean True.
    triggers = WORKFLOW.get("on", WORKFLOW.get(True))
    assert triggers["push"]["branches"] == [SOURCE_BRANCH]
    assert "workflow_dispatch" in triggers
    assert WORKFLOW_ENV["TARGET_REPOSITORY"] == "AgentFlocks/flocks"

    job = WORKFLOW["jobs"]["sync-dev-to-flocks"]
    assert job["if"] == "github.repository == 'AgentFlocks/flocksdev'"
    checkout, install_gitleaks, sync_step = job["steps"]
    assert checkout["uses"].startswith("actions/checkout@")
    # A shallow clone would make every ancestry check in the script come out wrong.
    assert checkout["with"] == {"ref": "${{ env.SOURCE_BRANCH }}", "fetch-depth": 0, "persist-credentials": False}
    assert "sha256sum --check" in install_gitleaks["run"]
    assert re.fullmatch(r"[0-9a-f]{64}", WORKFLOW_ENV["GITLEAKS_LINUX_X64_SHA256"])
    assert sync_step["run"] == "bash .github/scripts/sync-dev-to-flocks.sh"
    assert sync_step["env"] == {"FLOCKS_SYNC_TOKEN": "${{ secrets.FLOCKS_SYNC_TOKEN }}"}


def test_sync_never_force_pushes() -> None:
    for path in (WORKFLOW_PATH, SCRIPT_PATH):
        text = path.read_text(encoding="utf-8")
        assert "--force" not in text
        push_lines = [line for line in text.splitlines() if "git push" in line]
        assert all("+" not in line and " -f" not in line for line in push_lines), push_lines
    assert any("git push" in line for line in SCRIPT_PATH.read_text(encoding="utf-8").splitlines())


@needs_tools
@pytest.mark.parametrize("flocks_has_own_commit", [False, True])
def test_first_sync_opens_a_pr_from_a_new_branch(repos: SyncRepos, flocks_has_own_commit: bool) -> None:
    source_sha = repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    if flocks_has_own_commit:
        repos.commit_flocks(TARGET_BRANCH, "flocks.txt", "from flocks\n", "fix: flocks change")
    flocks_dev = repos.branch_sha(TARGET_BRANCH)

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    sync_branch = f"{PREFIX}-{source_sha[:12]}"
    assert repos.sync_branches() == {sync_branch: source_sha}
    assert repos.branch_sha(TARGET_BRANCH) == flocks_dev
    assert f"AgentFlocks/flocks dev only: {int(flocks_has_own_commit)}" in result.stdout
    assert "AgentFlocks/flocksdev dev only: 1" in result.stdout
    (scan,) = repos.gitleaks_calls()
    assert scan[:5] == ["git", "--no-banner", "--redact", "--verbose", "--config"]
    assert Path(scan[5]).parent == repos.runner_temp
    assert scan[6:] == [f"--log-opts={scan_range(source_sha)}", "."]
    assert Path(f"{repos.gitleaks_log}.config").read_text(encoding="utf-8") == "[extend]\nuseDefault = true\n"

    calls = repos.gh_calls()
    assert len(calls) == 2, calls
    assert calls[0].startswith(f"auth=sync-token pr list --repo AgentFlocks/flocks --base {TARGET_BRANCH} --state open")
    assert calls[1].startswith(
        f"auth=sync-token pr create --repo AgentFlocks/flocks --base {TARGET_BRANCH} --head {sync_branch}"
    )
    assert repos.prs() == [
        {"headRefName": sync_branch, "url": PR_URL.format(1), "isCrossRepository": False, "state": "OPEN"}
    ]
    body = Path(f"{repos.gh_log}.body").read_text(encoding="utf-8")
    assert "Create a merge commit" in body
    assert source_sha[:12] in body
    summary = repos.summary.read_text(encoding="utf-8")
    assert f"created at {source_sha[:12]}" in summary
    assert PR_URL.format(1) in summary
    assert list(repos.runner_temp.iterdir()) == []


@needs_tools
@pytest.mark.parametrize("flocks_ahead", [False, True])
def test_skips_when_flocks_dev_already_contains_source(repos: SyncRepos, flocks_ahead: bool) -> None:
    if flocks_ahead:
        repos.commit_flocks(TARGET_BRANCH, "flocks.txt", "from flocks\n", "fix: flocks change")

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to sync" in result.stdout
    assert repos.sync_branches() == {}
    assert [call.split()[1:3] for call in repos.gh_calls()] == [["pr", "list"]]
    assert repos.gitleaks_calls() == []


@needs_tools
def test_fast_forwards_the_open_sync_pr(repos: SyncRepos) -> None:
    first = repos.commit_source("feature.txt", "one\n", "feat: one")
    assert repos.run_sync().returncode == 0
    second = repos.commit_source("feature.txt", "one\ntwo\n", "feat: two")

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert repos.sync_branches() == {f"{PREFIX}-{first[:12]}": second}
    assert f"fast-forwarded to {second[:12]}" in result.stdout
    assert f"Existing sync PR: {PR_URL.format(1)}" in result.stdout
    assert len(repos.prs()) == 1


@needs_tools
@pytest.mark.parametrize("updated_on_github", [False, True])
def test_starts_a_new_branch_after_the_sync_pr_is_merged(repos: SyncRepos, updated_on_github: bool) -> None:
    first = repos.commit_source("feature.txt", "one\n", "feat: one")
    repos.commit_flocks(TARGET_BRANCH, "flocks.txt", "from flocks\n", "fix: flocks change")
    assert repos.run_sync().returncode == 0
    first_branch = f"{PREFIX}-{first[:12]}"
    first_head = repos.update_pr_branch(first_branch) if updated_on_github else first
    repos.merge_pr(PR_URL.format(1))
    second = repos.commit_source("feature.txt", "one\ntwo\n", "feat: two")

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    second_branch = f"{PREFIX}-{second[:12]}"
    # The new branch is the source commit itself: no merge commits made by the workflow pile up.
    assert repos.sync_branches() == {first_branch: first_head, second_branch: second}
    assert [(pr["headRefName"], pr["state"]) for pr in repos.prs()] == [
        (first_branch, "MERGED"),
        (second_branch, "OPEN"),
    ]
    assert repos.scanned_ranges()[-1] == scan_range(second)


@needs_tools
def test_stops_while_the_open_sync_pr_has_extra_commits(repos: SyncRepos) -> None:
    first = repos.commit_source("feature.txt", "one\n", "feat: one")
    repos.commit_flocks(TARGET_BRANCH, "flocks.txt", "from flocks\n", "fix: flocks change")
    assert repos.run_sync().returncode == 0
    first_branch = f"{PREFIX}-{first[:12]}"
    updated = repos.update_pr_branch(first_branch)
    second = repos.commit_source("feature.txt", "one\ntwo\n", "feat: two")

    stopped = repos.run_sync()

    assert stopped.returncode != 0
    assert f"::error title=Sync PR has extra commits::{PR_URL.format(1)}" in stopped.stdout
    assert repos.sync_branches() == {first_branch: updated}
    assert len(repos.prs()) == 1

    # The error says to merge that PR and re-run; the re-run has to pick the sync back up.
    repos.merge_pr(PR_URL.format(1))
    resumed = repos.run_sync()

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert repos.sync_branches()[f"{PREFIX}-{second[:12]}"] == second
    assert repos.prs()[-1]["state"] == "OPEN"


@needs_tools
def test_rerun_leaves_an_open_sync_pr_that_already_has_the_source(repos: SyncRepos) -> None:
    first = repos.commit_source("feature.txt", "one\n", "feat: one")
    repos.commit_flocks(TARGET_BRANCH, "flocks.txt", "from flocks\n", "fix: flocks change")
    assert repos.run_sync().returncode == 0
    first_branch = f"{PREFIX}-{first[:12]}"
    updated = repos.update_pr_branch(first_branch)
    calls_before = len(repos.gh_calls())

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"already contains {first[:12]}" in result.stdout
    assert repos.sync_branches() == {first_branch: updated}
    new_calls = repos.gh_calls()[calls_before:]
    assert len(new_calls) == 1 and " pr list " in new_calls[0], new_calls


@needs_tools
def test_ignores_a_fork_pr_that_reuses_the_sync_branch_name(repos: SyncRepos) -> None:
    source_sha = repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    sync_branch = f"{PREFIX}-{source_sha[:12]}"
    repos.add_pr(sync_branch, PR_URL.format(1), cross_repository=True)

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert repos.sync_branches() == {sync_branch: source_sha}
    assert repos.prs()[-1] == {
        "headRefName": sync_branch,
        "url": PR_URL.format(2),
        "isCrossRepository": False,
        "state": "OPEN",
    }
    assert f"Created sync PR: {PR_URL.format(2)}" in result.stdout
    assert PR_URL.format(1) not in repos.summary.read_text(encoding="utf-8")


@needs_tools
def test_secret_scan_finding_blocks_publishing(repos: SyncRepos) -> None:
    source_sha = repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")

    result = repos.run_sync(FAKE_GITLEAKS_EXIT="1")

    assert result.returncode != 0
    assert "::error title=Secret scan failed::" in result.stdout
    assert repos.scanned_ranges() == [scan_range(source_sha)]
    assert repos.sync_branches() == {}
    assert [call.split()[1:3] for call in repos.gh_calls()] == [["pr", "list"]]


@needs_tools
def test_missing_token_fails_before_touching_flocks(repos: SyncRepos) -> None:
    repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")

    result = repos.run_sync(FLOCKS_SYNC_TOKEN="")

    assert result.returncode != 0
    assert "::error title=Missing sync token::" in result.stdout
    assert repos.sync_branches() == {}
    assert repos.gh_calls() == []
    assert repos.gitleaks_calls() == []


@needs_tools
def test_rejected_push_is_reported_and_no_pr_is_opened(repos: SyncRepos) -> None:
    repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    hook = repos.target / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    result = repos.run_sync()

    assert result.returncode != 0
    assert "::error title=Push to AgentFlocks/flocks failed::" in result.stdout
    assert repos.sync_branches() == {}
    assert not any(" pr create " in call for call in repos.gh_calls())


@needs_tools
def test_git_authenticates_with_the_sync_token_over_credential_helpers(repos: SyncRepos) -> None:
    helper = "!f() { echo username=helper-user; echo password=helper-value; }; f"  # secret-guard: allow
    repos.global_config.write_text(f'[credential]\n\thelper = "{helper}"\n', encoding="utf-8")
    repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AuthRecorder)
    server.seen_auth = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = repos.run_sync(TARGET_GIT_URL=f"http://127.0.0.1:{server.server_port}/AgentFlocks/flocks.git")
    finally:
        server.shutdown()
        server.server_close()

    expected = "Basic " + base64.b64encode(f"x-access-token:{SYNC_TOKEN}".encode()).decode()
    assert result.returncode != 0, "the stub server rejects every request"
    assert {auth for auth in server.seen_auth if auth} == {expected}, server.seen_auth
    assert list(repos.runner_temp.iterdir()) == []


@needs_tools
@pytest.mark.parametrize("rollback_to_flocks_dev", [False, True])
def test_stops_when_source_is_rolled_back_under_an_open_sync_pr(repos: SyncRepos, rollback_to_flocks_dev: bool) -> None:
    base = repos.git(repos.source, "rev-parse", "HEAD")
    first = repos.commit_source("feature.txt", "one\n", "feat: one")
    assert repos.run_sync().returncode == 0
    sync_branch = f"{PREFIX}-{first[:12]}"
    dropped = repos.commit_source("feature.txt", "one\ntwo\n", "feat: two")
    assert repos.run_sync().returncode == 0
    # flocksdev dev is force-pushed back to an older commit; the workflow's checkout then sits on it.
    rolled_back = base if rollback_to_flocks_dev else first
    repos.git(repos.source, "checkout", "-q", "--detach", rolled_back)

    result = repos.run_sync()

    assert result.returncode != 0
    assert (
        f"::error title=Sync PR has commits missing from AgentFlocks/flocksdev dev::{PR_URL.format(1)}" in result.stdout
    )
    assert f"delete {sync_branch} in AgentFlocks/flocks" in result.stdout
    assert repos.sync_branches() == {sync_branch: dropped}

    # Following the error: close the PR, delete its branch, re-run.
    repos.close_pr(PR_URL.format(1))
    repos.git(repos.root, "--git-dir", str(repos.target), "update-ref", "-d", f"refs/heads/{sync_branch}")
    recovered = repos.run_sync()

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    if rollback_to_flocks_dev:
        assert "nothing to sync" in recovered.stdout
        assert repos.sync_branches() == {}
    else:
        assert repos.sync_branches() == {sync_branch: first}
        assert repos.prs()[-1]["state"] == "OPEN"


@needs_tools
def test_refuses_a_leftover_sync_branch_with_other_commits(repos: SyncRepos) -> None:
    source_sha = repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    assert repos.run_sync().returncode == 0
    sync_branch = f"{PREFIX}-{source_sha[:12]}"
    pushed = repos.commit_flocks(sync_branch, "extra.txt", "pushed to the PR\n", "fix: pushed to the PR")
    repos.close_pr(PR_URL.format(1))

    result = repos.run_sync()

    assert result.returncode != 0
    assert f"::error title=Sync branch has extra commits::{sync_branch}" in result.stdout
    assert repos.sync_branches() == {sync_branch: pushed}
    assert len(repos.prs()) == 1


@needs_tools
def test_reopens_a_pr_for_a_closed_sync_branch_still_at_the_source(repos: SyncRepos) -> None:
    source_sha = repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    assert repos.run_sync().returncode == 0
    sync_branch = f"{PREFIX}-{source_sha[:12]}"
    repos.close_pr(PR_URL.format(1))

    result = repos.run_sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"already contains {source_sha[:12]}" in result.stdout
    assert [(pr["headRefName"], pr["state"]) for pr in repos.prs()] == [
        (sync_branch, "CLOSED"),
        (sync_branch, "OPEN"),
    ]


@needs_tools
def test_scan_fails_closed_when_git_log_cannot_run(repos: SyncRepos) -> None:
    repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")
    # A git older than 2.36 rejects --remerge-diff; gitleaks would then report no leaks and exit 0.
    real_git = shutil.which("git", path=os.environ.get("PATH"))
    old_git = repos.root / "bin" / "git"
    old_git.write_text(
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--remerge-diff" ]; then echo "fatal: unrecognized argument: $arg" >&2; exit 128; fi\n'
        "done\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    old_git.chmod(0o755)

    result = repos.run_sync()

    assert result.returncode != 0
    assert "::error title=Secret scan failed::git log --remerge-diff" in result.stdout
    assert repos.gitleaks_calls() == []
    assert repos.sync_branches() == {}
    assert not any(" pr create " in call for call in repos.gh_calls())


@needs_tools
def test_scan_fails_closed_when_gitleaks_logs_an_error(repos: SyncRepos) -> None:
    repos.commit_source("feature.txt", "from flocksdev\n", "feat: flocksdev change")

    result = repos.run_sync(FAKE_GITLEAKS_STDERR='\x1b[90m8:25PM\x1b[0m \x1b[31mERR\x1b[0m error="stderr is not empty"')

    assert result.returncode != 0
    assert "::error title=Secret scan failed::" in result.stdout
    assert repos.sync_branches() == {}
    assert not any(" pr create " in call for call in repos.gh_calls())


@needs_tools
@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="needs the real gitleaks binary")
@pytest.mark.parametrize("with_key", [True, False])
def test_real_gitleaks_scans_what_a_merge_commit_adds(repos: SyncRepos, with_key: bool) -> None:
    (repos.root / "bin" / "gitleaks").unlink()
    # Built at runtime so this file does not trip secret scanners, the sync's own gate included.
    fake_key = "AKIA" + "Q3HZ7WPXV2LMN4TR"
    repos.git(repos.source, "switch", "-q", "-c", "side")
    repos.commit_source("side.txt", "side\n", "feat: side")
    repos.git(repos.source, "switch", "-q", SOURCE_BRANCH)
    repos.git(repos.source, "merge", "-q", "--no-ff", "--no-commit", "side")
    merged_value = fake_key if with_key else "not-a-secret"
    (repos.source / "merged.ini").write_text(f"aws_access_key_id = {merged_value}\n", encoding="utf-8")
    repos.git(repos.source, "add", "merged.ini")
    repos.git(repos.source, "commit", "-q", "-m", "Merge branch 'side'")
    merge_sha = repos.git(repos.source, "rev-parse", "HEAD")

    result = repos.run_sync()

    if with_key:
        assert result.returncode != 0
        assert "::error title=Secret scan failed::" in result.stdout
        assert "aws-access-token" in result.stdout
        assert fake_key not in result.stdout + result.stderr
        assert repos.sync_branches() == {}
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert repos.sync_branches() == {f"{PREFIX}-{merge_sha[:12]}": merge_sha}
