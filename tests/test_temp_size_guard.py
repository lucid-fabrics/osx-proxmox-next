"""The temp-size guard in conftest: it counts allocated blocks and names the biggest test dirs."""
from __future__ import annotations

import os
import shutil

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


def test_a_left_engine_copy_is_found_at_any_depth_and_other_files_are_not(tmp_path):
    (tmp_path / "gpuengine.abc123").mkdir()
    (tmp_path / "tmp.x" / "gpuengine.def456").mkdir(parents=True)
    (tmp_path / "gpuengine.file").write_text("a file is not a directory")
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "keep.txt").write_text("not an engine copy")
    found = guard.engine_copies(tmp_path)
    shutil.rmtree(tmp_path / "gpuengine.abc123")          # these are made up here: the fixture in conftest fails a test that leaves any
    shutil.rmtree(tmp_path / "tmp.x")
    assert found == ["gpuengine.abc123", "tmp.x/gpuengine.def456"]


def test_the_bytecode_cache_of_a_mac_is_not_an_engine_copy(tmp_path):
    (tmp_path / "Library" / "Caches" / "com.apple.python" / "tmp.x" / "gpuengine.abc123").mkdir(parents=True)
    assert guard.engine_copies(tmp_path) == []


def test_the_biggest_entries_of_a_dir_are_named(tmp_path):
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "f").write_bytes(b"x" * (3 * MB))
    (tmp_path / "small.txt").write_text("x")
    lines = guard.biggest_children(str(tmp_path))
    assert "big" in lines[0] and "small.txt" in lines[1]
    assert guard.biggest_children(str(tmp_path / "nope")) == []
