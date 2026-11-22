#!/usr/bin/env python3
"""Offline test for the parsing and statistics code of lib/benchctl.py. Run: python3 -I tests/test_bench.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("benchctl", os.path.join(HERE, "..", "lib", "benchctl.py"))
b = importlib.util.module_from_spec(sp); sp.loader.exec_module(b)

# statistics: odd and even n, None dropped, one value, nothing
s = b.summarize([46.0, 45.5, 47.5])
assert (s["n"], s["median"], s["min"], s["max"], s["mean"]) == (3, 46.0, 45.5, 47.5, 46.33), s
assert s["sd"] == 1.04, s
s = b.summarize([1, 2, None, 3, 10])
assert (s["n"], s["median"]) == (4, 2.5), s
s = b.summarize([5])
assert s["n"] == 1 and s["median"] == 5.0 and s["sd"] is None, s
assert b.summarize([None, "x", True])["n"] == 0
runs = [{"boot_to_ssh_s": 45.7, "drag_fps": 15.0}, {"boot_to_ssh_s": 46.1, "drag_fps": None}, {"boot_to_ssh_s": 45.9}]
sm = b.summarize_runs(runs)
assert sm["boot_to_ssh_s"]["median"] == 45.9 and sm["drag_fps"]["n"] == 1 and sm["resume_load_s"]["n"] == 0

# CLI output parsing
out = "run dir: /x/run-123456   ssh: ssh -p 10022 -i k root@127.0.0.1   renderer: GPU /usr/share/vulkan/icd.d/radeon_icd.x86_64.json\n"
assert b.parse_run_dir(out) == "/x/run-123456"
assert b.parse_renderer(out) == "GPU /usr/share/vulkan/icd.d/radeon_icd.x86_64.json"
assert b.gpu_path(b.parse_renderer(out)) == "RADV"
assert b.gpu_path("GPU /usr/share/vulkan/icd.d/intel_icd.json") == "ANV"
assert b.gpu_path("GPU /opt/x/mystery.json") == "GPU (mystery.json)"
assert b.gpu_path("lavapipe") == "lavapipe" and b.gpu_path("lavapipe", mesa_found=False) == "none" and b.gpu_path(None) == "none"
assert b.parse_renderer("run dir: /x/r   ssh: s   renderer: lavapipe\n") == "lavapipe"
st = "SETTIME: ssh 1.6 s after cont, method none, guest minus host 0.13 s (rtt 0.2), OK\n"
assert b.parse_settime(st) == {"resume_ssh_s": 1.6, "clock_off_s": 0.13, "time_ok": True}, b.parse_settime(st)
assert b.parse_settime("SETTIME: ssh 3.0 s after cont, method date, guest minus host -2.5 s (rtt 0.2), OUT OF 0.5 s\n")["time_ok"] is False
assert b.parse_settime("SETTIME: ssh never answered") == {}
assert b.parse_ssh_answered("x\nSAVE: ssh answered 66 s after QEMU started\n") == 66
assert b.count_frames("a\napple_gfx_new_frame\nb apple_gfx_new_frame 1\n") == 2
assert b.count_fatals("ok\nfatal: x\nall fine\nAbort here\n") == 2
assert b.cpu_model("processor\t: 0\nmodel name\t: AMD Ryzen 9   9950X 16-Core Processor\n") == "AMD Ryzen 9 9950X 16-Core Processor"
assert b.cpu_model("") == "unknown"

# screen parsing: a 40x30 screen, black with a cyan 20x10 block at (8, 12)
W, H = 40, 30
px = bytearray(W * H * 3)
for y in range(12, 22):
    for x in range(8, 28):
        o = (y * W + x) * 3; px[o:o + 3] = bytes((0, 255, 204))
data = b"P6\n%d %d\n255\n" % (W, H) + bytes(px)
r = b.parse_ppm(data)
assert r and r[0] == W and r[1] == H
assert b.parse_ppm(data[:-10]) is None and b.parse_ppm(b"junk") is None
assert b.page_bbox(r[2], W, H, b.PAGE_RGB["a"]) == (8, 12, 24, 20), b.page_bbox(r[2], W, H, b.PAGE_RGB["a"])
assert b.page_bbox(r[2], W, H, b.PAGE_RGB["b"]) is None
assert b.page_share(r[2], W, H, b.PAGE_RGB["a"]) > 0.05 and b.page_share(bytes(W * H * 3), W, H, b.PAGE_RGB["a"]) == 0

# report and table
host = {"cpu_model": "Test CPU", "host_threads": 6, "cores_given": 6, "ram_given": "8G", "gpu_path": "lavapipe", "qemu_md5": "455899b7cafe", "pcache": "ro"}
runs = [{"run": i + 1, "boot_to_ssh_s": 45.7 + i, "boot_to_dock_s": 90.0 + i, "safari_cold_draw_s": 30.0, "drag_fps": 14.5, "errors": []} for i in range(3)]
runs[2]["errors"] = ["resume: no SETTIME line"]
rep = b.build_report(host, runs, "abc1234", {"runs": 3}, "2026-11-20T00:00:00Z")
assert rep["summary"]["boot_to_ssh_s"]["median"] == 46.7 and rep["summary"]["resume_load_s"]["n"] == 0
tab = b.format_table(rep)
lines = tab.splitlines()
box = [l for l in lines if l[:1] in "┌│├└"]
assert len({len(l) for l in box}) == 1, "table rows differ in width"
assert "release abc1234" in tab and "QEMU md5 455899b7" in tab and "GPU path lavapipe" in tab and "run 3: resume: no SETTIME line" in tab
assert any(l.startswith("│ boot to ssh (s)") and "46.7" in l for l in lines)
assert chr(0x2014) not in tab
assert b.fmt(None) == "-" and b.fmt(14.5, "fps") == "14.50" and b.fmt(45.66) == "45.7"
print("bench tests ok")
