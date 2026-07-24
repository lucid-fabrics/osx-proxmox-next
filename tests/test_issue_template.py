"""Guard: issue templates must only reference commands this package installs (#106)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _entry_points() -> set:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"\[project\.scripts\](.*?)(?:\n\[|\Z)", text, re.DOTALL)
    assert section, "pyproject.toml has no [project.scripts] section"
    return set(re.findall(r"^([\w-]+)\s*=", section.group(1), re.MULTILINE))


def test_templates_reference_only_installed_commands():
    scripts = _entry_points()
    assert scripts
    templates = list((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    assert templates, "no issue templates found"
    for template in templates:
        for cmd in re.findall(r"`(osx-[\w-]+)", template.read_text(encoding="utf-8")):
            assert cmd in scripts, (
                f"{template.name} references {cmd!r}, which is not an installed "
                f"command (pyproject scripts: {sorted(scripts)})"
            )


def test_bug_report_version_field_uses_real_command():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    match = re.search(r"description: Run `(osx-[\w-]+) --version`", text)
    assert match, "bug_report.yml version field must instruct running an installed command with --version"
    assert match.group(1) in _entry_points()
