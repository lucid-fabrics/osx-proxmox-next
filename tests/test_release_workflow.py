"""The release workflow and the gitleaks allowlist: a dispatch must not bump main, and no input is spliced into a shell line."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def run_blocks(text: str) -> list[str]:
    """Every `run: |` body, so the assertions see the shell lines and not the env: mappings."""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        header = re.match(r"^(\s*)run: \|$", lines[i])
        if header is None:
            i += 1
            continue
        indent = len(header.group(1))
        i += 1
        body = []
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body))
    return blocks


def test_the_release_job_only_runs_on_a_push():
    assert "  release:\n    if: github.event_name == 'push'\n" in workflow()


def test_the_dispatch_inputs_reach_the_shell_through_env_only():
    text = workflow()
    for block in run_blocks(text):
        assert "${{ inputs." not in block, block
    assert "TAG: ${{ inputs.tag }}" in text
    assert "RENDERER_URL: ${{ inputs.renderer_asset_url }}" in text
    assert "RENDERER_SHA256: ${{ inputs.renderer_sha256 }}" in text


def test_an_empty_renderer_sha_falls_back_to_the_pin():
    text = workflow()
    assert '. arm64/pins.env && printf %s "$RENDERER_SHA256"' in text
    assert "arm64/pins.env" in text and "exit 1" in text


def test_the_gitleaks_allowlist_does_not_blanket_the_install_tree():
    text = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "paths = ['''arm64/lib/install/''']" not in text
    assert "hackkey\\.pem" in text
    assert 'key="ApImg4Ticket"' in text


def step_script(name: str) -> str:
    """The dedented `run: |` body of the step called `name`, taken from the workflow text."""
    lines = workflow().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    i = start + 1
    while lines[i].strip() != "run: |":
        i += 1
    indent = len(lines[i]) - len(lines[i].lstrip())
    i += 1
    body = []
    while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
        body.append(lines[i])
        i += 1
    return textwrap.dedent("\n".join(body))


BUMP_STEP = "Determine version bump from commits"
NOTES_STEP = "Generate release notes"
BASH = shutil.which("bash") or "bash"


def git_env(home: Path) -> dict[str, str]:
    """No global or system git config (hooks, gitignore) and a fixed identity, so Mac and CI behave the same."""
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


class Repo:
    """A tiny work repository with a bare `origin`, every command run under `git_env`."""

    def __init__(self, tmp_path: Path):
        self.env = git_env(tmp_path)
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.out = tmp_path / "github_output"
        self.out.write_text("", encoding="utf-8")
        self.last: subprocess.CompletedProcess | None = None
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=tmp_path)
        self.git("init", "-q", "-b", "main")
        self.git("remote", "add", "origin", str(self.origin))

    def git(self, *args: str, cwd: Path | None = None) -> str:
        done = subprocess.run(
            ["git", *args], cwd=cwd or self.work, env=self.env, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    def commit(self, subject: str, tag: str | None = None) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", subject)
        if tag:
            self.git("tag", tag)

    def push(self) -> None:
        self.git("push", "-q", "origin", "main")

    def run(self, script: str, sha: str | None = None) -> subprocess.CompletedProcess:
        """Run under `bash -eo pipefail`, the shell GitHub Actions uses for a `run:` step."""
        env = dict(self.env, GITHUB_SHA=sha or self.git("rev-parse", "HEAD"), GITHUB_OUTPUT=str(self.out))
        return subprocess.run(
            [BASH, "-eo", "pipefail", "-c", script], cwd=self.work, env=env, check=True, capture_output=True, text=True
        )

    def bump(self, sha: str | None = None) -> str:
        """Run the bump step and return the `bump=` output."""
        done = self.run(step_script(BUMP_STEP), sha)
        self.last = done
        values = [ln.split("=", 1)[1] for ln in self.out.read_text(encoding="utf-8").splitlines() if ln.startswith("bump=")]
        assert len(values) == 1, (values, done.stdout, done.stderr)
        return values[0]

    def previous_tag_for_notes(self) -> str:
        line = next(ln for ln in step_script(NOTES_STEP).splitlines() if ln.startswith("PREV_TAG=$(git describe"))
        return self.run(f'{line}\nprintf %s "$PREV_TAG"').stdout


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path)
    r.commit("chore: first", "v1.0.0")
    return r


def test_a_fix_carrying_a_beta_tag_still_bumps_patch(repo: Repo):
    repo.commit("fix(x): a fix", "v1.0.1-beta.1")
    repo.push()
    assert repo.bump() == "patch"


def test_a_beta_tag_on_a_later_chore_does_not_hide_the_fix_before_it(repo: Repo):
    repo.commit("fix(x): a fix")
    repo.commit("chore: later", "v1.0.1-beta.1")
    repo.push()
    assert repo.bump() == "patch"


def test_a_non_version_tag_does_not_hide_the_fix_before_it(repo: Repo):
    repo.commit("fix(x): a fix")
    repo.commit("chore: later", "arm64-renderer-1")
    repo.push()
    assert repo.bump() == "patch"


def test_the_bump_step_logs_the_stable_tag_it_found(repo: Repo):
    repo.commit("chore: beta", "v1.0.1-beta.1")
    repo.push()
    repo.bump()
    assert "Previous release tag: v1.0.0" in repo.last.stdout


def test_a_run_for_a_commit_main_has_moved_past_bumps_nothing(repo: Repo):
    repo.commit("fix(x): old")
    old = repo.git("rev-parse", "HEAD")
    repo.commit("fix(x): newer")
    repo.push()
    assert repo.bump(sha=old) == "none"
    assert "::warning::main has moved past this commit" in repo.last.stdout


def test_push_and_dispatch_runs_use_different_concurrency_groups():
    """A dispatch must not cancel a pending push run, so the group has to include the event name."""
    match = re.search(r"^concurrency:\n  group: (.+)$", workflow(), re.MULTILINE)
    assert match, "no workflow-level concurrency group"
    assert "github.event_name" in match.group(1)


def test_the_bump_step_fails_and_writes_no_bump_when_the_fetch_fails(repo: Repo):
    repo.commit("fix(x): a fix")
    repo.push()
    repo.git("remote", "set-url", "origin", str(repo.origin.parent / "missing.git"))
    with pytest.raises(subprocess.CalledProcessError):
        repo.bump()
    assert "bump=" not in repo.out.read_text(encoding="utf-8")


def test_feat_bumps_minor(repo: Repo):
    repo.commit("fix: a")
    repo.commit("feat(x): b")
    repo.push()
    assert repo.bump() == "minor"


def test_a_bang_subject_bumps_major(repo: Repo):
    repo.commit("feat: b")
    repo.commit("refactor(x)!: c")
    repo.push()
    assert repo.bump() == "major"


def test_only_chores_bump_nothing(repo: Repo):
    repo.commit("chore: a")
    repo.commit("docs: b")
    repo.push()
    assert repo.bump() == "none"


def test_no_tag_at_all_reads_the_whole_history(tmp_path: Path):
    r = Repo(tmp_path)
    r.commit("chore: first")
    r.commit("feat: second")
    r.push()
    assert r.bump() == "minor"


def test_the_release_notes_range_starts_at_the_stable_tag(repo: Repo):
    repo.commit("fix(x): a fix", "v1.0.1-beta.1")
    repo.commit("chore: release commit")
    assert repo.previous_tag_for_notes() == "v1.0.0"
