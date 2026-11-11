"""The guest-side scripts (staging in recovery, first boots) run against stubbed macOS tools."""
from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from osx_proxmox_next import nullmoth_engine as engine

DATA = Path(engine.__file__).resolve().parent / "data" / "nullmoth"
CARD = '    "vendor-id" = <de100000>\n'


def render(name: str, values: dict[str, str]) -> str:
    text = (DATA / name).read_text()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


class Sandbox:
    """A fake macOS: stub commands that log their calls, and a script whose absolute paths point into tmp."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.log = tmp / "calls.log"
        self.log.write_text("")
        self.payload = tmp / "payload"
        self.payload.mkdir()
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "LOG_FILE": str(self.log)}

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/sh\necho \"{name} $*\" >>\"$LOG_FILE\"\n{body}\n")
        path.chmod(0o755)

    def calls(self) -> list[str]:
        return [ln.rstrip() for ln in self.log.read_text().splitlines()]

    def run(self, script: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(script)], capture_output=True, text=True, env=self.env, timeout=60)


def tarball(path: Path, install: str = "exit 0") -> str:
    body = f"#!/bin/bash\necho install-ran NULLMOTH_FW_RUN=${{NULLMOTH_FW_RUN:-none}} >>\"$LOG_FILE\"\n{install}\n".encode()
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("pkgroot/install.sh")
        info.size = len(body)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(body))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FirstBoot:
    def __init__(self, tmp: Path, install: str = "exit 0", fw: bool = True, card: bool = True, kexts: int = 5,
                 displays: int = 0, sha: str = "") -> None:
        self.sb = Sandbox(tmp)
        self.base = tmp / "Application Support" / "nullmoth"
        self.base.mkdir(parents=True)
        self.name = "nullmoth-nvidia-2.9.tar.gz"
        real_sha = tarball(self.base / self.name, install)
        if fw:
            (self.base / "fw.run").write_bytes(b"firmware")
        text = render("nm_firstboot.sh", {"TARBALL": self.name, "TARBALL_SHA256": sha or real_sha,
                                          "FW_RUN": "fw.run" if fw else "", "FW_RUN_SHA256": "f" * 64})
        for old, new in (('BASE="/Library/Application Support/OSXProxmoxNext/nullmoth"', f'BASE="{self.base}"'),
                         ('PLIST="/Library/LaunchDaemons/', f'PLIST="{tmp}/'), ('LOG="/var/log/osx-proxmox-next-nullmoth.log"', f'LOG="{tmp}/guest.log"'),
                         ("/bin/launchctl", "launchctl"), ("/usr/bin/pmset", "pmset"), ("/usr/sbin/ioreg", "ioreg"),
                         ("/usr/bin/kmutil", "kmutil"), ("/usr/sbin/ipconfig", "ipconfig"), ("/sbin/shutdown", "shutdown"),
                         ("/usr/bin/shasum -a 256", "shasum -a 256"), ("/bin/bash ", "bash "),
                         ("LOAD_WAIT=300", "LOAD_WAIT=5")):
            assert old in text, old
            text = text.replace(old, new)
        self.script = tmp / "firstboot.sh"
        self.script.write_text(text)
        self.plist = tmp / "com.osxproxmoxnext.nullmoth-firstboot.plist"
        self.plist.write_text("plist")
        sb = self.sb
        for name in ("launchctl", "pmset", "shutdown"):
            sb.stub(name, "exit 0")
        # diskutil info reports the mount point only while the payload disk is attached
        sb.stub("diskutil", f'[ "$1" = info ] && [ -d "{sb.payload}" ] && echo "   Mount Point:               {sb.payload}"; exit 0')
        sb.stub("ioreg", f"printf '{CARD}' " if card else "exit 0")
        sb.stub("kmutil", "".join(f"echo com.nullmoth.K{i}\n" for i in range(kexts)) + "exit 0")
        sb.stub("ipconfig", 'case "$2" in en1) echo 10.0.0.9;; *) exit 1;; esac')
        sb.stub("ioreg", (f"printf '{CARD}' ; " if card else "") + "".join('echo \\"IODisplayEDID\\"; ' for _ in range(displays)) + "exit 0")
        (sb.bin / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (sb.bin / "sleep").chmod(0o755)
        (self.base / "state").unlink(missing_ok=True)

    def run(self):
        return self.sb.run(self.script)

    @property
    def result(self) -> str:
        path = self.sb.payload / "result.txt"
        return path.read_text().strip() if path.exists() else ""

    @property
    def state(self) -> str:
        path = self.base / "state"
        return path.read_text().strip() if path.exists() else ""


def test_first_boot_installs_then_verifies_and_removes_itself(tmp_path):
    fb = FirstBoot(tmp_path, displays=1)
    assert fb.run().returncode == 0
    calls = fb.sb.calls()
    assert "launchctl load -w /System/Library/LaunchDaemons/ssh.plist" in calls
    assert "launchctl load -w /System/Library/LaunchDaemons/com.apple.screensharing.plist" in calls
    assert "pmset -a sleep 0" in calls and f"install-ran NULLMOTH_FW_RUN={fb.base}/fw.run" in calls
    assert fb.result == "installed" and fb.state == "verify" and "shutdown -r now" in calls
    assert not (fb.base / fb.name).exists() and not (fb.base / "fw.run").exists()  # freed once installed
    assert fb.plist.exists()  # it runs again after the restart
    assert fb.run().returncode == 0
    assert fb.result == "verified: kexts=5 display=yes ip=10.0.0.9" and fb.state == "done"
    assert "shutdown -h now" in fb.sb.calls() and not fb.plist.exists()
    parsed = engine.parse_result(fb.result)
    assert parsed["state"] == "verified" and parsed["kexts"] == 5 and parsed["ip"] == "10.0.0.9"
    fb.run()  # a later boot does nothing and keeps the job gone
    assert fb.state == "done" and not fb.plist.exists()


def test_first_boot_reports_no_display_and_works_without_the_firmware_package(tmp_path):
    fb = FirstBoot(tmp_path, fw=False)
    fb.run()
    assert "NULLMOTH_FW_RUN=none" in " ".join(fb.sb.calls())
    fb.run()
    assert "display=no" in fb.result


def test_a_failed_driver_install_is_reported_once_and_ends_the_job(tmp_path):
    fb = FirstBoot(tmp_path, install='echo "   STOP: kmutil refused" >&2; exit 1')
    fb.run()
    assert fb.result.startswith("failed: the driver installer stopped:") and fb.state == "failed"
    assert "shutdown -h now" in fb.sb.calls() and not fb.plist.exists() and '"' not in fb.result
    fb.run()
    assert fb.state == "failed" and fb.sb.calls().count("shutdown -h now") == 1


def test_no_card_and_a_bad_checksum_stop_before_the_installer_runs(tmp_path):
    (tmp_path / "a").mkdir()
    fb = FirstBoot(tmp_path / "a", card=False)
    fb.run()
    assert fb.result == "failed: macOS does not see the NVIDIA card" and "install-ran" not in " ".join(fb.sb.calls())
    assert not any("ssh.plist" in c for c in fb.sb.calls())  # remote access is only turned on once the card is there
    other = tmp_path / "b"
    other.mkdir()
    fb = FirstBoot(other, sha="0" * 64)
    fb.run()
    assert fb.result == "failed: the driver package checksum does not match" and "install-ran" not in " ".join(fb.sb.calls())


def test_a_driver_that_does_not_load_fails_the_verification(tmp_path):
    fb = FirstBoot(tmp_path, kexts=2)
    fb.run()
    fb.run()
    assert fb.result == "failed: the driver did not load after the restart (kexts loaded: 2, needed 4)"
    assert fb.state == "failed" and not fb.plist.exists()


def test_a_leftover_volumes_folder_is_not_taken_for_the_payload_disk(tmp_path):
    fb = FirstBoot(tmp_path)
    leftover = tmp_path / "Volumes" / "OSXPNIGPU"
    leftover.mkdir(parents=True)
    fb.sb.stub("diskutil", f'[ "$1" = info ] && echo "   Mount Point:               {fb.sb.payload}"; exit 0')
    fb.run()
    assert fb.result == "installed"  # written through the reported mount point, nowhere else
    assert not any(leftover.iterdir())


def test_without_a_payload_disk_nothing_is_reported_and_nothing_is_powered_off(tmp_path):
    fb = FirstBoot(tmp_path, card=False)
    (fb.sb.payload).rmdir()
    fb.run()
    assert fb.state == "failed" and not (fb.sb.payload / "result.txt").exists() and not fb.plist.exists()
    assert "shutdown -h now" not in fb.sb.calls()  # nobody is waiting for a report: the guest stays up
    ok = tmp_path / "ok"
    ok.mkdir()
    fb = FirstBoot(ok)
    fb.sb.payload.rmdir()
    fb.run()
    assert fb.state == "verify" and "shutdown -r now" in fb.sb.calls()  # the driver still needs its restart
    fb.run()
    assert fb.state == "done" and not fb.plist.exists() and "shutdown -h now" not in fb.sb.calls()


def test_verification_tells_the_host_the_restart_worked_before_it_waits(tmp_path):
    fb = FirstBoot(tmp_path, kexts=2)
    fb.run()
    seen = []
    real = fb.sb.payload / "result.txt"
    fb.sb.stub("sleep", f'cat "{real}" >>"{tmp_path}/seen.txt"')
    fb.run()
    assert "verifying" in (tmp_path / "seen.txt").read_text().split()


class Stage:
    def __init__(self, tmp: Path, fw: bool = True, consent_ok: bool = True, data: bool = True, corrupt: bool = False) -> None:
        self.sb = Sandbox(tmp)
        src = self.sb.payload
        self.name = "nullmoth-nvidia-2.9.tar.gz"
        (src / self.name).write_bytes(b"tarball")
        (src / "fw.run").write_bytes(b"firmware")
        (src / "firstboot.sh").write_text("#!/bin/sh\n")
        (src / "com.osxproxmoxnext.nullmoth-firstboot.plist").write_text("plist")
        text = render("nm_stage.sh", {"TARBALL": self.name, "FW_RUN": "fw.run" if fw else ""})
        self.data = tmp / "Data"
        (self.data / "Library").mkdir(parents=True)
        if not data:
            (self.data / "Library").rmdir()
        text = text.replace('D="/Volumes/MACOS - Data"', f'D="{self.data}"').replace('"/Volumes/Data"', f'"{tmp}/none"')
        text = text.replace("/sbin/halt", "halt").replace("chown -R 0:0", "chown_stub")
        script = src / "stage.sh"
        script.write_text(text)
        self.script = script
        for name in ("diskutil", "halt", "chown_stub"):
            self.sb.stub(name, "exit 0")
        self.sb.stub("spctl", "exit 0" if consent_ok else "echo denied; exit 1")
        if corrupt:
            self.sb.stub("cmp", "exit 1")

    def run(self):
        return self.sb.run(self.script)

    @property
    def result(self) -> str:
        return (self.sb.payload / "result.txt").read_text().strip()


def test_staging_copies_everything_turns_off_consent_and_halts(tmp_path):
    st = Stage(tmp_path)
    st.run()
    base = st.data / "Library/Application Support/OSXProxmoxNext/nullmoth"
    assert st.result == "staged"
    assert (base / st.name).read_bytes() == b"tarball" and (base / "fw.run").read_bytes() == b"firmware"
    assert (base / "firstboot.sh").exists() and (st.data / "Library/LaunchDaemons/com.osxproxmoxnext.nullmoth-firstboot.plist").exists()
    calls = st.sb.calls()
    assert "spctl kext-consent disable" in calls and calls[-1] == "halt"
    assert any(c.startswith("chown_stub") for c in calls)


def test_staging_without_the_firmware_package(tmp_path):
    st = Stage(tmp_path, fw=False)
    st.run()
    assert st.result == "staged" and not (st.data / "Library/Application Support/OSXProxmoxNext/nullmoth/fw.run").exists()


@pytest.mark.parametrize("kwargs,needle", [
    ({"data": False}, "failed: the installed macOS data volume is not mounted"),
    ({"consent_ok": False}, "failed: could not turn off the extension approval prompt (denied"),
    ({"corrupt": True}, "failed: the copied driver package differs from the original"),
])
def test_staging_failures_are_reported_and_halt(tmp_path, kwargs, needle):
    st = Stage(tmp_path, **kwargs)
    st.run()
    assert st.result.startswith(needle) and st.sb.calls()[-1] == "halt"
