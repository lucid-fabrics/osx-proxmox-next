"""The temp-size guard in conftest: it counts allocated blocks and names the biggest test dirs."""
from __future__ import annotations

import os

import pytest

import temp_size as guard

MB = 1024 * 1024


def test_a_sparse_file_costs_what_it_uses_not_what_it_claims(tmp_path):
    with open(tmp_path / "sparse.img", "wb") as f:
        f.truncate(900 * MB)
    if os.stat(tmp_path / "sparse.img").st_blocks * 512 >= 100 * MB:
        pytest.skip("this filesystem has no sparse files")
    assert guard.allocated_bytes(str(tmp_path)) < 10 * MB


def test_a_written_file_is_counted_and_a_hard_link_only_once(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * (2 * MB))
    os.link(tmp_path / "a.bin", tmp_path / "b.bin")
    size = guard.allocated_bytes(str(tmp_path))
    assert 2 * MB <= size < 3 * MB


def test_the_report_orders_test_dirs_by_size_and_skips_the_current_link(tmp_path):
    for name, mb in (("test_small0", 1), ("test_big0", 3)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_bytes(b"x" * (mb * MB))
    os.symlink(tmp_path / "test_big0", tmp_path / "test_bigcurrent")
    total, sizes = guard.temp_size_report(str(tmp_path))
    assert [name for _n, name in sizes] == ["test_big0", "test_small0"]
    assert total >= 4 * MB


def test_a_missing_base_dir_is_an_empty_report(tmp_path):
    assert guard.temp_size_report(str(tmp_path / "nope")) == (0, [])


def test_nothing_is_reported_under_both_limits():
    assert guard.temp_size_problems(100 * MB, [(10 * MB, "test_a0")], 500, 50) == []


def test_the_total_limit_names_the_largest_dirs():
    lines = guard.temp_size_problems(600 * MB, [(40 * MB, "test_big0"), (1 * MB, "test_small0")], 500, 50)
    assert "600 MB" in lines[0] and "test_big0" in lines[1] and "Stub the build" in lines[-1]


def test_one_big_test_dir_fails_even_when_the_total_is_small():
    lines = guard.temp_size_problems(80 * MB, [(60 * MB, "test_wedged0")], 500, 50)
    assert lines and "test_wedged0" in lines[1]


_LEFT = []


def test_a_bash_run_leaves_an_engine_copy_behind(tmp_path):
    copy = tmp_path / "gpuengine.abc123"
    copy.mkdir()
    (copy / "host_setup.py").write_bytes(b"x" * 100_000)
    (tmp_path / "keep.txt").write_text("not an engine copy")
    _LEFT.append(tmp_path)
    assert copy.exists()


def test_the_engine_copies_of_the_test_before_are_swept_and_other_files_are_not():
    assert _LEFT, "the test above has to run first"
    assert not (_LEFT[0] / "gpuengine.abc123").exists()
    assert (_LEFT[0] / "keep.txt").exists()
