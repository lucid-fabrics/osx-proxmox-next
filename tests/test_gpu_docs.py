"""The docs, the README and the BIOS data must agree."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from osx_proxmox_next import amd_host, bios_guide, gpu_setup, nullmoth

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs/docs/guides"
DATA = bios_guide.load_data()
PAGES = {p.stem: p.read_text() for p in (DOCS / "gpu/bios").glob("*.md")}


def test_every_qr_target_has_a_page():
    slugs = {v["doc"] for v in DATA["vendors"].values()} | {"generic"}
    assert slugs == set(PAGES)
    for slug in slugs:
        assert bios_guide.doc_url(DATA, slug if slug == "generic" else next(k for k, v in DATA["vendors"].items() if v["doc"] == slug)).endswith(f"/guides/gpu/bios/{slug}")


@pytest.mark.parametrize("key", sorted(DATA["vendors"]))
def test_pages_carry_every_path_and_flag_unverified_ones(key):
    ven = DATA["vendors"][key]
    page = PAGES[ven["doc"]]
    for setting, entry in ven["paths"].items():
        assert entry["path"] in page, (key, setting)
        row = next(ln for ln in page.splitlines() if entry["path"] in ln)
        if entry["verified"]:
            assert entry["source"] in row and "usually under" not in row
        else:
            assert "usually under" in row and "| no |" in row
    assert ven["enter"] in page


def test_pages_link_the_official_manual_and_the_guide():
    for slug, page in PAGES.items():
        assert "gpu-passthrough.md" in page and "BIOS search" in page or slug == "generic"
    assert "https://www.asus.com/support/" in PAGES["asus"]
    assert "Names to look for" in PAGES["generic"]


def test_sidebar_lists_the_new_pages():
    sidebar = (REPO / "docs/sidebars.ts").read_text()
    assert "'guides/gpu-passthrough'" in sidebar
    for slug in PAGES:
        assert f"'guides/gpu/bios/{slug}'" in sidebar


def test_guide_has_the_notice_the_matrix_and_the_anchor():
    guide = (DOCS / "gpu-passthrough.md").read_text()
    assert "## Supported cards" in guide
    for line in nullmoth.NOTICE_BODY.splitlines():
        if line:
            assert line in guide
    assert nullmoth.NOTICE_TITLE in guide
    for fam in amd_host._load().families:
        assert fam.label in guide
    for _label, _ids, reason in amd_host._load().deny:
        assert reason in guide
    assert gpu_setup._PRE_TURING in guide
    assert gpu_setup.SUPPORTED_DOC.endswith("gpu-passthrough#supported-cards")
    assert "not been tested on real hardware" in guide


def test_readme_points_to_the_guide_and_has_no_invalid_argument():
    readme = (REPO / "README.md").read_text()
    assert "amd_iommu=on" not in readme
    assert "osx-next-cli gpu-setup" in readme and "docs/guides/gpu-passthrough" in readme
    assert "#nvidia-nullmoth-beta" not in readme and "#-gpu-passthrough" in readme
    section = readme[readme.index("## 🎮 GPU Passthrough"):readme.index("## ⚡ Performance Tips")]
    assert len(section.splitlines()) < 30
    assert "RX 7000" in readme


def test_no_em_dash_in_the_new_files():
    src = REPO / "src/osx_proxmox_next"
    new_modules = ["amd_host", "bios_guide", "host_setup", "gpu_attach", "gpu_setup", "gpu_setup_cli", "_gpu_mixin", "gpu", "nullmoth"]
    paths = [DOCS / "gpu-passthrough.md", *(DOCS / "gpu/bios").glob("*.md"), *(f for f in (src / "data/gpu").glob("*") if f.is_file()),
             *(src / f"{m}.py" for m in new_modules), src / "screens/gpu_panel.py", REPO / "scripts/embed_gpu_engines.py"]
    for path in paths:
        assert "\u2014" not in path.read_text(), path
    bash = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
    assert "\u2014" not in bash[bash.index("# ── GPU walkthrough begin ──"):bash.index("# ── GPU walkthrough end ──")]
    readme = (REPO / "README.md").read_text()
    assert "\u2014" not in readme[readme.index("## 🎮 GPU Passthrough"):readme.index("## ⚡ Performance Tips")]


def test_no_mention_of_the_other_project():
    src = REPO / "src/osx_proxmox_next"
    mine = [src / f"{m}.py" for m in ("amd_host", "bios_guide", "host_setup", "gpu_attach", "gpu_setup", "gpu_setup_cli", "_gpu_mixin")]
    mine += [f for f in (src / "data/gpu").glob("*") if f.is_file()]
    mine += [DOCS / "gpu-passthrough.md", *(DOCS / "gpu/bios").glob("*.md"), REPO / "README.md"]
    for path in mine:
        assert "luchina" not in path.read_text().lower(), path
    bash = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
    assert "luchina" not in bash[bash.index("# ── GPU walkthrough begin ──"):bash.index("# ── GPU walkthrough end ──")].lower()
