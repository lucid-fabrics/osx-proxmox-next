"""Host-side configuration for GPU passthrough: detect, preview, apply, undo, verify.

Standard library only and no imports from this package: the bash installer embeds
this file verbatim (tests/test_gpu_parity.py diffs the two) and runs it with
``python3 - <command>``. Everything that touches the host takes a ``root``
(default ``/``) and a ``run`` callable so tests can use a fake tree.

Rules this module keeps:
  * the boot loader is read from what booted (efibootmgr, /proc/cmdline), never
    from which files exist;
  * every write is previewed as a diff, backed up with a timestamp, recorded in
    the state file and rolled back on error;
  * ``pcie_acs_override`` and ``amd_iommu=on`` are never written;
  * the host is never rebooted from here.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = "/var/lib/osx-proxmox-next"
STATE_FILE = STATE_DIR + "/gpu-setup.json"
BACKUP_ROOT = STATE_DIR + "/backups"
LOGIN_HINT = "/etc/profile.d/osx-proxmox-next-gpu.sh"
GRUB_DROPIN = "/etc/default/grub.d/osx-proxmox-next.cfg"
KERNEL_CMDLINE = "/etc/kernel/cmdline"
MODULES_FILE = "/etc/modules-load.d/osx-proxmox-next-vfio.conf"
MODPROBE_FILE = "/etc/modprobe.d/osx-proxmox-next-vfio.conf"
BIND_SCRIPT = "/etc/initramfs-tools/scripts/init-top/a-osx-proxmox-next-gpu-bind"
VR_MODULES_FILE = "/etc/modules-load.d/osx-proxmox-next-vendor-reset.conf"
VR_UDEV_FILE = "/etc/udev/rules.d/99-osx-proxmox-next-vendor-reset.rules"
INITRAMFS_MODULES = "/etc/initramfs-tools/modules"
VR_REPO = "https://github.com/gnif/vendor-reset"
VR_SHA = "cbb4dd50926337e0f9fe1dd1e8a74aa5cb3448a7"
VR_VERSION = "0.1.1"
VR_SRC = "/usr/src/vendor-reset-" + VR_VERSION
VFIO_MODULES = ("vfio", "vfio_iommu_type1", "vfio_pci")
STEPS = ("Scan", "Choose", "BIOS", "Host", "Reboot", "Verify", "VM", "First boot")
BAR_WARN_BYTES = 256 * 1024 * 1024
LONG_TIMEOUT = 1800  # boot image and boot loader rebuilds take minutes
SECURE_BOOT_VAR = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
OWN_FILES = (GRUB_DROPIN, MODULES_FILE, MODPROBE_FILE, BIND_SCRIPT, VR_MODULES_FILE, VR_UDEV_FILE)
HEADER = "# Written by osx-proxmox-next (GPU passthrough setup). Undo with: gpu-setup --undo\n"


# ── small helpers ────────────────────────────────────────────────────

def default_run(argv, timeout=20):
    """Run a command and return (exit code, stdout+stderr). (127, '') when it cannot start."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return res.returncode, (res.stdout or "") + (res.stderr or "")


def _p(root, path):
    return Path(root) / path.lstrip("/")


def _read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _hex(path):
    text = _read(path)
    try:
        return int(text.strip(), 16) if text is not None else None
    except ValueError:
        return None


def _sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()


def _now():
    return time.strftime("%Y%m%d-%H%M%S")


def _tokens(text):
    return (text or "").split()


# ── facts ────────────────────────────────────────────────────────────

def pci_devices(root="/"):
    """Every PCI device: bdf, vendor, device, class, boot_vga, driver, group."""
    base = _p(root, "/sys/bus/pci/devices")
    out = []
    if not base.is_dir():
        return out
    for dev in sorted(base.iterdir()):
        link = dev / "driver"
        group = dev / "iommu_group"
        out.append({
            "bdf": dev.name,
            "vendor": _hex(dev / "vendor"),
            "device": _hex(dev / "device"),
            "class": _hex(dev / "class"),
            "boot_vga": _hex(dev / "boot_vga") == 1,
            "driver": link.resolve().name if link.exists() else None,
            "group": group.resolve().name if group.exists() else None,
        })
    return out


def detect_bootloader(root="/", run=default_run):
    """'grub', 'systemd-boot' or 'unknown', from what booted."""
    if not _p(root, "/sys/firmware/efi").exists():
        return "grub"
    code, out = run(["efibootmgr", "-v"])
    if code == 0:
        current = re.search(r"BootCurrent:\s*([0-9A-Fa-f]{4})", out)
        lines = [ln for ln in out.splitlines() if re.match(r"Boot[0-9A-Fa-f]{4}", ln)]
        if current:
            lines = [ln for ln in lines if ln.startswith("Boot" + current.group(1).upper())] or lines
        for ln in lines:
            low = ln.lower()
            if "systemd-boot" in low:
                return "systemd-boot"
            if "grubx64" in low or "shimx64" in low:
                return "grub"
    cmdline = _read(_p(root, "/proc/cmdline")) or ""
    if cmdline.startswith("BOOT_IMAGE="):
        return "grub"
    code, out = run(["proxmox-boot-tool", "status"])
    if code == 0:
        m = re.search(r"configured with:\s*(\S+)", out)
        if m:
            return "systemd-boot" if m.group(1).lower().startswith("uefi") else "grub"
    return "unknown"


def _dmi(root, name):
    return (_read(_p(root, "/sys/class/dmi/id/" + name)) or "").strip()


def _secure_boot(root, code, mokutil_out):
    """Secure Boot state. When it cannot be read it counts as ON: the safe answer for vendor-reset."""
    if code == 0:
        return "SecureBoot enabled" in mokutil_out
    try:
        raw = _p(root, SECURE_BOOT_VAR).read_bytes()
    except OSError:
        return _p(root, "/sys/firmware/efi").exists()
    return len(raw) < 5 or raw[4] == 1


def detect_facts(root="/", run=default_run, env=None):
    env = os.environ if env is None else env
    cpuinfo = _read(_p(root, "/proc/cpuinfo")) or ""
    vendor_id = re.search(r"^vendor_id\s*:\s*(\S+)", cpuinfo, re.M)
    cpu = "unknown"
    if vendor_id:
        cpu = "amd" if "AuthenticAMD" in vendor_id.group(1) else "intel" if "GenuineIntel" in vendor_id.group(1) else "unknown"
    flags = re.search(r"^flags\s*:\s*(.*)$", cpuinfo, re.M)
    devices = pci_devices(root)
    groups = _p(root, "/sys/kernel/iommu_groups")
    tables = _p(root, "/sys/firmware/acpi/tables")
    code, mok = run(["mokutil", "--sb-state"])
    secure = _secure_boot(root, code, mok)
    _, pve = run(["pveversion"])
    _, dmesg = run(["dmesg"])
    bar_errors = sorted({m.group(1) for m in re.finditer(
        r"pci (\S+): BAR \d+(?: \[[^\]]*\])?: (?:no space for|failed to assign)", dmesg)})
    return {
        "cpu_vendor": cpu,
        "virtualization": bool(flags and re.search(r"\b(vmx|svm)\b", flags.group(1))),
        "board_vendor": _dmi(root, "board_vendor") or _dmi(root, "sys_vendor"),
        "sys_vendor": _dmi(root, "sys_vendor"),
        "board_name": _dmi(root, "board_name") or _dmi(root, "product_name"),
        "product_name": _dmi(root, "product_name"),
        "bios_vendor": _dmi(root, "bios_vendor"),
        "efi": _p(root, "/sys/firmware/efi").exists(),
        "bootloader": detect_bootloader(root, run),
        "kernel": (run(["uname", "-r"])[1].strip() if root == "/" else "") or os.uname().release,
        "pve": pve.strip().splitlines()[0] if pve.strip() else "",
        "cmdline": (_read(_p(root, "/proc/cmdline")) or "").strip(),
        "secure_boot": secure,
        "bar_errors": bar_errors,
        "iommu_groups": len(list(groups.iterdir())) if groups.is_dir() else 0,
        "dmar": (tables / "DMAR").exists(),
        "ivrs": (tables / "IVRS").exists(),
        "ssh": bool(env.get("SSH_CONNECTION")),
        "displays": [d for d in devices if d["class"] is not None and d["class"] >> 16 == 0x03],
        "pci": devices,
    }


# ── kernel command line ──────────────────────────────────────────────

_GRUB_RE = re.compile(r"""^\s*(GRUB_CMDLINE_LINUX(?:_DEFAULT)?)=(?:"([^"]*)"|'([^']*)'|(\S*))""", re.M)


def _grub_apply(values, text):
    """Evaluate the assignments in order: a self-reference appends, anything else replaces."""
    for m in _GRUB_RE.finditer(text):
        name = m.group(1)
        tokens = _tokens(m.group(2) or m.group(3) or m.group(4))
        keeps = any(t in ("$" + name, "${" + name + "}") for t in tokens)
        fresh = [t for t in tokens if not t.startswith("$")]
        values[name] = values.get(name, []) + fresh if keeps else fresh


def _grub_tokens(text):
    values = {}
    _grub_apply(values, text)
    return values.get("GRUB_CMDLINE_LINUX_DEFAULT", []) + values.get("GRUB_CMDLINE_LINUX", [])


def configured_cmdline(bootloader, root="/"):
    """Kernel arguments the boot loader is configured to pass on the next boot."""
    if bootloader == "systemd-boot":
        return _tokens(_read(_p(root, KERNEL_CMDLINE)))
    if bootloader != "grub":
        return []
    files = [_p(root, "/etc/default/grub")]
    dropins = _p(root, "/etc/default/grub.d")
    if dropins.is_dir():
        files += sorted(dropins.glob("*.cfg"))
    values = {}
    for path in files:
        _grub_apply(values, _read(path) or "")
    return values.get("GRUB_CMDLINE_LINUX_DEFAULT", []) + values.get("GRUB_CMDLINE_LINUX", [])


def wanted_kernel_args(facts, target):
    args = []
    if facts["cpu_vendor"] == "intel":
        args.append("intel_iommu=on")
    args.append("iommu=pt")
    if target.get("boot_vga"):
        args.append("initcall_blacklist=sysfb_init")
    return args


def _has_arg(tokens, wanted):
    key = wanted.split("=", 1)[0]
    if key == "iommu" or key == "intel_iommu":
        return any(t.split("=", 1)[0] == key and t != key + "=off" for t in tokens)
    return wanted in tokens


def plan_kernel_args(facts, target, root, plan):
    wanted = wanted_kernel_args(facts, target)
    configured = configured_cmdline(facts["bootloader"], root)
    live = _tokens(facts["cmdline"])
    missing = [a for a in wanted if not _has_arg(configured, a) and not _has_arg(live, a)]
    pending = [a for a in wanted if _has_arg(configured, a) and not _has_arg(live, a)]
    if pending:
        plan["notes"].append("Already configured but not active until you reboot: " + " ".join(pending))
        plan["reboot_required"] = True
    for bad in ("amd_iommu=on", "pcie_acs_override"):
        if any(t.startswith(bad) for t in configured + live):
            plan["notes"].append(
                f"'{bad}' is on the kernel command line. It is not written or removed by this tool "
                + ("(amd_iommu=on is not a valid value and does nothing)." if bad == "amd_iommu=on" else
                   "(it weakens isolation; see the docs for the risk)."))
    if not missing:
        plan["skipped"].append("Kernel arguments are already set")
        return
    if facts["bootloader"] == "grub":
        path = _p(root, GRUB_DROPIN)
        own = _grub_tokens(_read(path) or "")
        tokens = own + [m for m in missing if m not in own]
        content = HEADER + 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT ' + " ".join(tokens) + '"\n'
        _add_change(plan, root, GRUB_DROPIN, content, "Kernel arguments for passthrough: " + " ".join(missing))
        plan["commands"].append({"argv": ["update-grub"], "reason": "Rebuild the GRUB menu"})
        plan["refresh"].append("grub")
    elif facts["bootloader"] == "systemd-boot":
        old = _read(_p(root, KERNEL_CMDLINE))
        if old is None:
            plan["refusals"].append(f"{KERNEL_CMDLINE} is missing, so the kernel arguments cannot be set safely.")
            return
        content = old.rstrip("\n") + " " + " ".join(missing) + "\n"
        _add_change(plan, root, KERNEL_CMDLINE, content, "Kernel arguments for passthrough: " + " ".join(missing))
        plan["commands"].append({"argv": ["proxmox-boot-tool", "refresh"], "reason": "Copy the command line to the boot partition"})
        plan["refresh"].append("systemd-boot")
    else:
        plan["manual"].append(
            "Could not tell which boot loader this host uses. Add these arguments to the kernel command line "
            "yourself: " + " ".join(missing))
    plan["reboot_required"] = True
    if "initcall_blacklist=sysfb_init" in missing:
        plan["warnings"].append(
            "The chosen card is the host console GPU. After the next reboot the screen plugged into the host goes "
            "dark once the boot loader hands over. SSH and the web UI keep working.")


# ── plan helpers ─────────────────────────────────────────────────────

def _add_change(plan, root, path, content, reason, mode=0o644):
    old = _read(_p(root, path))
    if old == content:
        return
    plan["changes"].append({"path": path, "old": old, "new": content, "reason": reason, "mode": mode})


def new_plan():
    return {"changes": [], "commands": [], "notes": [], "warnings": [], "hard_warnings": [], "refusals": [],
            "manual": [], "skipped": [], "refresh": [], "reboot_required": False, "binding": "", "initramfs": False}


def _modules_present(root):
    found = set()
    files = [_p(root, "/etc/modules")]
    d = _p(root, "/etc/modules-load.d")
    if d.is_dir():
        files += [f for f in sorted(d.glob("*.conf")) if "/" + f.relative_to(Path(root)).as_posix() != MODULES_FILE]
    for f in files:
        for line in (_read(f) or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                found.add(line.replace("-", "_"))
    return found


def plan_modules(root, plan):
    present = _modules_present(root)
    if "vfio_virqfd" in present:
        plan["notes"].append("/etc/modules lists vfio_virqfd. It no longer exists (merged into vfio in kernel 6.2) "
                             "and only logs a harmless error at boot. Left alone.")
    missing = [m for m in VFIO_MODULES if m not in present]
    if not missing:
        plan["skipped"].append("VFIO modules are already listed")
        return
    content = HEADER + "".join(m + "\n" for m in VFIO_MODULES)
    _add_change(plan, root, MODULES_FILE, content, "Load the VFIO modules at boot")


def _softdeps(target):
    names = ["amdgpu", "radeon"] if target["vendor"] == "amd" else ["nouveau", "nvidia"]
    names.append("snd_hda_intel")
    if any((f.get("class") or 0) >> 8 in (0x0C03, 0x0C80) for f in target["functions"]):
        names.append("xhci_pci")
        if target["vendor"] == "nvidia":
            names.append("i2c_nvidia_gpu")
    return names


_IDS_RE = re.compile(r"\bids=([0-9A-Fa-f:,]+)")


_OPT_RE = re.compile(r"^\s*options\s+vfio[-_]pci\b")


def _live_ids(text):
    """Ids of the active (not commented) `options vfio-pci ids=` lines, in order."""
    found = []
    for line in text.splitlines():
        m = _IDS_RE.search(line) if _OPT_RE.match(line) else None
        if m:
            found += [i.lower() for i in m.group(1).split(",") if i and i.lower() not in found]
    return found


def _merge_ids(text, ids):
    """Add *ids* to every active vfio-pci ids line; comments and other lines stay as they are."""
    have = _live_ids(text)
    union = have + [i for i in ids if i not in have]
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if _OPT_RE.match(line) and _IDS_RE.search(line):
            lines[idx] = _IDS_RE.sub("ids=" + ",".join(union), line, count=1)
    return "".join(lines)


def _existing_ids_lines(root):
    d = _p(root, "/etc/modprobe.d")
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.conf")):
        posix = "/" + f.relative_to(Path(root)).as_posix()
        text = _read(f) or ""
        if posix != MODPROBE_FILE and _live_ids(text):
            out.append((posix, text))
    return out


def plan_binding(facts, target, root, plan):
    ids = []
    for fn in target["functions"]:
        ident = "%04x:%04x" % (fn["vendor"], fn["device"])
        if ident not in ids:
            ids.append(ident)
    mine = {fn["bdf"] for fn in target["functions"]}
    twins = sorted(d["bdf"] for d in facts["pci"] if d["bdf"] not in mine and d["vendor"] is not None
                   and "%04x:%04x" % (d["vendor"], d["device"]) in ids)
    existing = _existing_ids_lines(root)
    softdep = "".join("softdep %s pre: vfio-pci\n" % n for n in _softdeps(target))
    if twins:
        plan["binding"] = "per-device"
        plan["notes"].append(
            "Another device on this host has the same PCI id (%s). Binding by id would take it too, so this "
            "card is bound by address instead." % ", ".join(twins))
        covered = [p for p, text in existing if any(i in _live_ids(text) for i in ids)]
        if covered:
            plan["refusals"].append(
                "%s already binds the id %s to vfio-pci for every device with that id, including the other "
                "card. Remove that id from the file first." % (covered[0], ", ".join(ids)))
            return
        _add_change(plan, root, MODPROBE_FILE, HEADER + softdep, "Load vfio-pci before the GPU drivers")
        _add_change(plan, root, BIND_SCRIPT, _bind_script([f["bdf"] for f in target["functions"]]),
                    "Bind this card by PCI address early in boot", mode=0o755)
    else:
        plan["binding"] = "ids"
        merged_all = True
        for path, text in existing:
            new = _merge_ids(text, ids)
            if new != text:
                merged_all = False
                _add_change(plan, root, path, new, "Add the card's ids to your existing vfio-pci line")
        body = HEADER + softdep
        if not existing:
            body = HEADER + "options vfio-pci ids=%s disable_vga=1\n" % ",".join(ids) + softdep
        elif merged_all:
            plan["notes"].append("The card's ids are already in your vfio-pci options.")
        _add_change(plan, root, MODPROBE_FILE, body, "Bind the card to vfio-pci before its driver loads")


def _bind_script(bdfs):
    lines = "\n".join('  echo vfio-pci > /sys/bus/pci/devices/%s/driver_override 2>/dev/null || true' % shlex.quote(b)
                      for b in bdfs)
    return (
        "#!/bin/sh\n" + HEADER +
        'PREREQ=""\nprereqs() { echo "$PREREQ"; }\ncase "$1" in prereqs) prereqs; exit 0 ;; esac\n'
        "# Mark this card's functions for vfio-pci before any GPU driver can claim them.\n"
        + lines + "\n/sbin/modprobe -q vfio-pci || true\nexit 0\n")


def _reset_ids(target):
    fn0 = [f for f in target["functions"] if f["bdf"].endswith(".0")] or target["functions"][:1]
    return ["%04x" % f["device"] for f in fn0]


def plan_vendor_reset(facts, target, root, run, plan):
    reset = target.get("reset", "none")
    if not reset.startswith("vendor-reset"):
        return
    if reset == "vendor-reset-warn":
        plan["hard_warnings"].append(
            "Radeon VII / Pro VII with vendor-reset: stopping and starting a VM that holds this card without "
            "rebooting the host has corrupted host storage on Proxmox 9 (vendor-reset issue 112, IOMMU timeouts, "
            "then NVMe errors and a read-only root filesystem). Reboot the host between VM runs.")
    if facts["secure_boot"]:
        plan["manual"].append(
            "Secure Boot is on, so the unsigned vendor-reset module would be rejected and it was NOT installed. "
            "Either turn Secure Boot off in the BIOS, or enroll the DKMS key: run 'mokutil --import "
            "/var/lib/dkms/mok.pub', reboot, finish the blue MokManager screen, then run gpu-setup again. "
            "Without vendor-reset this card may only work once per host boot.")
        return
    code, out = run(["dkms", "status"])
    built = any(re.search(r"vendor-reset/[^,]+,\s*" + re.escape(facts["kernel"]) + r"[^:]*:\s*installed", ln)
                for ln in out.splitlines()) if code == 0 else False
    if built:
        plan["skipped"].append("vendor-reset is already built for this kernel")
    else:
        kernel = facts["kernel"]
        plan["commands"].append({"argv": ["apt-get", "install", "-y", "dkms", "git", "proxmox-default-headers",
                                          "proxmox-headers-" + kernel], "reason": "Packages needed to build vendor-reset"})
        plan["commands"].append({"argv": ["bash", "-c",
            "[ -d %s/.git ] || git clone %s %s; git -C %s checkout %s" % (VR_SRC, VR_REPO, VR_SRC, VR_SRC, VR_SHA)],
            "reason": "Fetch vendor-reset at the pinned commit " + VR_SHA[:12]})
        plan["commands"].append({"argv": ["bash", "-c",
            "dkms status -m vendor-reset -v %s | grep -q . || dkms add -m vendor-reset -v %s; "
            "dkms install -m vendor-reset -v %s" % (VR_VERSION, VR_VERSION, VR_VERSION)],
            "reason": "Build and install the vendor-reset module", "tag": "vendor-reset"})
    _add_change(plan, root, VR_MODULES_FILE, HEADER + "vendor-reset\n", "Load vendor-reset at boot")
    existing = _read(_p(root, INITRAMFS_MODULES)) or ""
    if not re.search(r"^\s*vendor-reset\s*$", existing, re.M):
        _add_change(plan, root, INITRAMFS_MODULES, existing.rstrip("\n") + ("\n" if existing else "") + "vendor-reset\n",
                    "Load vendor-reset before vfio-pci binds the card")
    rules = HEADER + "".join(
        'ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x%04x", ATTR{device}=="0x%s", RUN+="/bin/sh -c '
        "'/sbin/modprobe vendor-reset; echo device_specific > /sys$env{DEVPATH}/reset_method'\"\n"
        % (target["functions"][0]["vendor"], dev) for dev in _reset_ids(target))
    _add_change(plan, root, VR_UDEV_FILE, rules, "Use the vendor-reset method to reset the card")


def plan_console(facts, target, opts, plan):
    others = [d for d in facts["displays"] if not d["bdf"].startswith(target["bdf"] + ".")]
    if not target.get("boot_vga"):
        return
    if not others:
        if not opts.get("allow_no_console"):
            plan["refusals"].append(
                "This card is the only display adapter in the host. Binding it to a VM leaves the host with no "
                "screen. Type the confirmation to continue, and make sure you can reach this host over SSH.")
        elif not facts["ssh"]:
            plan["warnings"].append("You are not on an SSH session. Make sure you can log in over the network "
                                    "before you reboot.")
        plan["warnings"].append("Rescue if the host will not boot: in the GRUB menu press 'e', delete the arguments "
                                "added by this tool from the linux line, press Ctrl-X, then run gpu-setup --undo.")
    else:
        plan["notes"].append("The chosen card is the console GPU. Another display adapter exists, so the host keeps "
                             "a screen only if the BIOS uses it as the primary display (see the BIOS step).")


def plan_host(facts, target, opts=None, root="/", run=default_run):
    """Everything this host still needs for *target* (see module doc for the shape)."""
    opts = opts or {}
    plan = new_plan()
    plan_console(facts, target, opts, plan)
    plan_kernel_args(facts, target, root, plan)
    plan_modules(root, plan)
    plan_binding(facts, target, root, plan)
    plan_vendor_reset(facts, target, root, run, plan)
    boot_files = (GRUB_DROPIN, KERNEL_CMDLINE)
    plan["initramfs"] = any(c["path"] not in boot_files for c in plan["changes"]) or \
        any(c.get("tag") == "vendor-reset" for c in plan["commands"])
    plan["reboot_required"] = plan["reboot_required"] or bool(plan["changes"])
    if plan["initramfs"]:
        plan["commands"].append({"argv": ["update-initramfs", "-u", "-k", "all"], "reason": "Rebuild the boot image"})
        plan["refresh"].append("initramfs")
    return plan


def render_preview(plan):
    """Plain-text preview: a unified diff per file, then the commands."""
    out = []
    for ch in plan["changes"]:
        out.append("--- %s  (%s)" % (ch["path"], ch["reason"]))
        old = (ch["old"] or "").splitlines(keepends=True)
        new = ch["new"].splitlines(keepends=True)
        out += [ln.rstrip("\n") for ln in difflib.unified_diff(
            old, new, fromfile=ch["path"] if ch["old"] is not None else "(new file)", tofile=ch["path"], n=1)]
    for cmd in plan["commands"]:
        out.append("run: %s  (%s)" % (" ".join(shlex.quote(a) for a in cmd["argv"]), cmd["reason"]))
    return "\n".join(out)


# ── state ────────────────────────────────────────────────────────────

def boot_id(root="/"):
    return (_read(_p(root, "/proc/sys/kernel/random/boot_id")) or "").strip()


def reboot_pending(state, root="/"):
    """True while a reboot is needed and this boot is still the one the changes were made in."""
    return bool(state.get("reboot_needed")) and state.get("boot_id", "") == boot_id(root)


def rebooted_since_apply(state, root="/"):
    return bool(state.get("boot_id")) and state["boot_id"] != boot_id(root)


def new_state():
    return {"version": 1, "step": 1, "done": [], "skipped": [], "gpu": None, "vm": None, "display": "console",
            "ack_nullmoth": False, "verify_retry": False, "host_changes": [], "vm_records": [], "bios_saved": False,
            "resume_cmd": "osx-next-cli gpu-setup", "vendor_reset_installed": False, "finished": False,
            "started": _now(), "updated": _now()}


def load_state(root="/"):
    text = _read(_p(root, STATE_FILE))
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return {**new_state(), **data} if isinstance(data, dict) else None


def save_state(state, root="/"):
    state["updated"] = _now()
    path = _p(root, STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def delete_state(root="/"):
    try:
        _p(root, STATE_FILE).unlink()
    except OSError:
        pass


def complete_step(state, step, skipped=False):
    """Mark *step* done (or skipped) and move to the next one."""
    keep, drop = ("skipped", "done") if skipped else ("done", "skipped")
    state[drop] = [n for n in state[drop] if n != step]
    if step not in state[keep]:
        state[keep].append(step)
    state["step"] = min(step + 1, len(STEPS))


def goto_step(state, step):
    """Go back (or forward) to *step*: later steps are no longer done."""
    state["step"] = step
    for name in ("done", "skipped"):
        state[name] = [n for n in state[name] if n < step]


def build_target(root, bdf, vendor, reset="none"):
    """The chosen card in the shape plan_host and verify_host want."""
    funcs = [{"bdf": d["bdf"], "vendor": d["vendor"], "device": d["device"], "class": d["class"]}
             for d in pci_devices(root) if d["bdf"].startswith(bdf + ".")]
    boot = any(d["boot_vga"] for d in pci_devices(root) if d["bdf"].startswith(bdf + "."))
    return {"vendor": vendor, "bdf": bdf, "boot_vga": boot, "reset": reset, "functions": funcs}


def hint_text(state):
    name = STEPS[min(max(state["step"], 1), len(STEPS)) - 1]
    return "GPU setup paused at step %d of %d (%s). Continue with: %s" % (
        state["step"], len(STEPS), name, state.get("resume_cmd") or "osx-next-cli gpu-setup")


def set_login_hint(state, root="/"):
    path = _p(root, LOGIN_HINT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Written by osx-proxmox-next. Removed when the GPU setup finishes or is undone.\n"
                    "[ -t 1 ] && echo %s\n" % shlex.quote(hint_text(state)))


def clear_login_hint(root="/"):
    try:
        _p(root, LOGIN_HINT).unlink()
    except OSError:
        pass


# ── apply / undo ─────────────────────────────────────────────────────

def _backup_path(root, stamp, path):
    return _p(root, BACKUP_ROOT) / stamp / path.lstrip("/").replace("/", "__")


def _safe_path(path):
    """Only absolute paths under /etc, without `..`: nothing this tool writes lives elsewhere."""
    return isinstance(path, str) and path.startswith("/etc/") and ".." not in path.split("/")


def _write_file(root, path, content, mode):
    target = _p(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".osx-tmp")
    try:
        tmp.write_text(content)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _restore(root, entry, check_drift=False):
    """Put one file back. Returns "" or the reason it was left alone."""
    target = _p(root, entry["path"])
    if not _safe_path(entry["path"]):
        return "%s is not a path this tool manages" % entry["path"]
    if check_drift and target.exists() and _sha(_read(target)) != entry.get("sha_after"):
        return "%s was changed after this tool wrote it, so it was left alone (backup: %s)" % (
            entry["path"], entry.get("backup") or "none")
    try:
        if entry["existed"]:
            if not Path(entry["backup"]).exists():
                return "the backup of %s is missing (%s)" % (entry["path"], entry["backup"])
            shutil.copy2(entry["backup"], target)
        else:
            target.unlink(missing_ok=True)
    except OSError as exc:
        return "could not restore %s: %s" % (entry["path"], exc)
    entry["reverted"] = True
    return ""


_REFRESH = {"grub": ["update-grub"], "systemd-boot": ["proxmox-boot-tool", "refresh"],
            "initramfs": ["update-initramfs", "-u", "-k", "all"]}


def _refresh_commands(kinds):
    return [_REFRESH[k] for k in ("grub", "systemd-boot", "initramfs") if k in kinds]


def _run_refreshes(kinds, state, run):
    """Rebuild boot config for *kinds*. Failures stay in state["pending_refresh"] so a later undo retries them."""
    errors = []
    pending = []
    for kind in ("grub", "systemd-boot", "initramfs"):
        if kind not in kinds:
            continue
        code, out = run(_REFRESH[kind], timeout=LONG_TIMEOUT)
        if code != 0:
            pending.append(kind)
            errors.append("%s failed: %s" % (" ".join(_REFRESH[kind]), out.strip()[-200:]))
    state["pending_refresh"] = pending
    return errors


def apply_plan(plan, state, root="/", run=default_run, confirmed=False):
    """Write the plan. Returns {ok, error, rolled_back, reboot_required, backup_dir}."""
    if plan["refusals"]:
        return {"ok": False, "error": plan["refusals"][0], "rolled_back": False}
    if plan["hard_warnings"] and not confirmed:
        return {"ok": False, "error": "A hard warning needs a typed confirmation first.", "rolled_back": False}
    bad = [c["path"] for c in plan["changes"] if not _safe_path(c["path"])]
    if bad:
        return {"ok": False, "error": "Refusing to write outside /etc: " + ", ".join(bad), "rolled_back": False}
    stamp = _now()
    n = 1
    while _p(root, BACKUP_ROOT).joinpath(stamp).exists():
        n += 1
        stamp = "%s-%d" % (_now(), n)
    done = []
    try:
        for ch in plan["changes"]:
            target = _p(root, ch["path"])
            existed = target.exists()
            backup = ""
            if existed:
                bp = _backup_path(root, stamp, ch["path"])
                bp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, bp)
                backup = str(bp)
            entry = {"path": ch["path"], "existed": existed, "backup": backup, "stamp": stamp,
                     "sha_before": _sha(_read(target)) if existed else "", "sha_after": _sha(ch["new"]),
                     "reverted": False}
            state["host_changes"].append(entry)
            done.append(entry)
            save_state(state, root)
            _write_file(root, ch["path"], ch["new"], ch.get("mode", 0o644))
        for cmd in plan["commands"]:
            code, out = run(cmd["argv"], timeout=LONG_TIMEOUT)
            if code != 0:
                raise RuntimeError("%s failed: %s" % (" ".join(cmd["argv"]), out.strip()[-400:]))
            if cmd.get("tag") == "vendor-reset":
                state["vendor_reset_installed"] = True
        if plan["reboot_required"]:
            state["reboot_needed"] = True
            state["boot_id"] = boot_id(root)
        save_state(state, root)
    except BaseException as exc:  # noqa: BLE001  (Ctrl-C included: never leave half-applied files)
        for entry in reversed(done):
            _restore(root, entry)
        errors = _run_refreshes(set(plan["refresh"]), state, run)
        save_state(state, root)
        if not isinstance(exc, (OSError, RuntimeError)):
            raise
        extra = [e for e in errors if e not in str(exc)]
        return {"ok": False, "error": str(exc) + "".join(" " + e for e in extra), "rolled_back": True}
    return {"ok": True, "error": "", "rolled_back": False, "reboot_required": plan["reboot_required"],
            "backup_dir": str(_p(root, BACKUP_ROOT) / stamp)}


def undo_host(state, root="/", run=default_run):
    """Restore every file this tool changed, newest first. Safe to run twice; a failed rebuild is retried."""
    kinds = set(state.get("pending_refresh", []))
    restored = []
    errors = []
    for entry in reversed(state["host_changes"]):
        if entry.get("reverted"):
            continue
        problem = _restore(root, entry, check_drift=True)
        if problem:
            errors.append(problem)
            continue
        restored.append(entry["path"])
        if entry["path"] == GRUB_DROPIN:
            kinds.add("grub")
        elif entry["path"] == KERNEL_CMDLINE:
            kinds.add("systemd-boot")
        else:
            kinds.add("initramfs")
    if state.get("vendor_reset_installed"):
        code, out = run(["dkms", "remove", "vendor-reset/" + VR_VERSION, "--all"], timeout=600)
        if code == 0:
            state["vendor_reset_installed"] = False
        else:
            errors.append("dkms remove failed: " + out.strip()[-200:])
    errors += _run_refreshes(kinds, state, run)
    save_state(state, root)
    return {"ok": not errors, "restored": restored, "errors": errors, "reboot_required": bool(restored)}


# ── verify ───────────────────────────────────────────────────────────

def _check(cid, label, status, detail="", fix=""):
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix}


def verify_host(target, root="/", run=default_run):
    """Post-reboot checks. status is ok, fail, warn or info; fix says where to go on a failure."""
    facts = detect_facts(root, run)
    out = []
    configured = configured_cmdline(facts["bootloader"], root)
    live = _tokens(facts["cmdline"])
    wanted = wanted_kernel_args(facts, target)
    not_live = [a for a in wanted if _has_arg(configured, a) and not _has_arg(live, a)]
    absent = [a for a in wanted if not _has_arg(live, a)]
    out.append(_check("cmdline", "Kernel arguments are active",
                      "ok" if not absent else "fail",
                      "Reboot the host: configured but not active: " + " ".join(not_live) if not_live else
                      "Missing: " + " ".join(absent) if absent else "", "host" if absent else ""))
    active = facts["iommu_groups"] > 0
    out.append(_check("iommu", "IOMMU is on", "ok" if active else "fail",
                      "" if active else ("The firmware table is there but the kernel did not enable it: check the "
                                         "kernel arguments." if facts["dmar"] or facts["ivrs"] else
                                         "The motherboard hides the IOMMU from the OS: turn on VT-d / AMD IOMMU in the BIOS."),
                      "" if active else ("host" if facts["dmar"] or facts["ivrs"] else "bios")))
    _, dmesg = run(["dmesg"])
    if dmesg:
        remap = re.search(r"Interrupt remapping enabled|Enabled IRQ remapping", dmesg, re.I)
        out.append(_check("irq", "Interrupt remapping", "ok" if remap else "warn",
                          "" if remap else "Not reported in dmesg. Passthrough still works but is less isolated."))
    mine = {f["bdf"] for f in target["functions"]}
    devs = {d["bdf"]: d for d in facts["pci"]}
    wrong = [f["bdf"] + " (" + (devs.get(f["bdf"], {}).get("driver") or "no driver") + ")"
             for f in target["functions"] if devs.get(f["bdf"], {}).get("driver") != "vfio-pci"]
    out.append(_check("driver", "The card is held by vfio-pci", "ok" if not wrong else "fail",
                      "" if not wrong else "Not bound: " + ", ".join(wrong), "" if not wrong else "host"))
    group = (devs.get(target["bdf"] + ".0") or {}).get("group")
    stray = []
    storage_net = []
    for d in facts["pci"]:
        if group and d["group"] == group and d["bdf"] not in mine:
            cls = (d["class"] or 0) >> 8
            if cls in (0x0604, 0x0600):
                continue
            stray.append(d["bdf"])
            if (cls >> 8) in (0x01, 0x02):
                storage_net.append(d["bdf"])
    if group is None:
        out.append(_check("group", "IOMMU group holds only the card", "fail", "The card has no IOMMU group.", "bios"))
    else:
        detail = ""
        if stray:
            detail = ("Also in the group: " + ", ".join(stray) + ". Move the card to another slot first. "
                      "pcie_acs_override can split groups but it makes the isolation a pretence, so this tool never "
                      "applies it.")
            if storage_net:
                detail += " The group holds storage or network devices (" + ", ".join(storage_net) + ")."
        out.append(_check("group", "IOMMU group holds only the card", "fail" if stray else "ok", detail,
                          "acs" if stray else ""))
    mods = _read(_p(root, "/proc/modules")) or ""
    loaded = {ln.split()[0] for ln in mods.splitlines() if ln.strip()}
    missing_mods = [m for m in VFIO_MODULES if m not in loaded]
    out.append(_check("modules", "VFIO modules are loaded", "ok" if not missing_mods else "fail",
                      "Not loaded: " + ", ".join(missing_mods) if missing_mods else "", "host" if missing_mods else ""))
    unsafe = (_read(_p(root, "/sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts")) or "").strip()
    if unsafe == "Y":
        out.append(_check("unsafe", "Interrupt isolation", "warn",
                          "allow_unsafe_interrupts is on (weakened isolation). This tool does not change it."))
    if target.get("reset", "none").startswith("vendor-reset"):
        method = (_read(_p(root, "/sys/bus/pci/devices/%s.0/reset_method" % target["bdf"])) or "").split()
        ok = "device_specific" in method
        out.append(_check("reset", "Reset method (AMD reset bug)", "ok" if ok else "warn",
                          "" if ok else "vendor-reset is not active for this card, so it may only work once per host boot.",
                          "" if ok else "host"))
    bar = _bar_size(_p(root, "/sys/bus/pci/devices/%s.0/resource" % target["bdf"]), 0 if target["vendor"] == "amd" else 1)
    if bar is not None:
        label = "BAR size"
        if bar > BAR_WARN_BYTES:
            out.append(_check("bar", label, "warn", "%d MiB. Larger than 256 MiB is untested for macOS guests; "
                              "turn Resizable BAR off in the BIOS if the VM will not start." % (bar // 1048576)))
        else:
            out.append(_check("bar", label, "info", "%d MiB" % (bar // 1048576)))
    return out


def _bar_size(path, index):
    lines = (_read(path) or "").splitlines()
    try:
        start, end, _ = lines[index].split()
        size = int(end, 16) - int(start, 16) + 1
    except (IndexError, ValueError):
        return None
    return size if size > 1 else None


# ── command line (used by the bash installer) ────────────────────────

def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    root = os.environ.get("GPU_ROOT", "/")
    payload = json.loads(sys.stdin.read() or "{}") if cmd not in ("facts", "state", "hint-clear", "undo", "reboot-pending", "mark-reboot", "rebooted") else {}
    if cmd == "facts":
        _emit(detect_facts(root))
    elif cmd == "target":
        _emit(build_target(root, payload["bdf"], payload["vendor"], payload.get("reset", "none")))
    elif cmd == "step":
        state = load_state(root) or new_state()
        (goto_step(state, payload["step"]) if payload.get("goto")
         else complete_step(state, payload["step"], bool(payload.get("skipped"))))
        save_state(state, root)
    elif cmd == "plan":
        _emit(plan_host(detect_facts(root), payload["target"], payload.get("opts"), root))
    elif cmd == "preview":
        sys.stdout.write(render_preview(payload) + "\n")
    elif cmd == "apply":
        state = load_state(root) or new_state()
        res = apply_plan(payload["plan"], state, root, confirmed=bool(payload.get("confirmed")))
        _emit(res)
        return 0 if res["ok"] else 1
    elif cmd == "undo":
        state = load_state(root)
        if state is None:
            _emit({"ok": True, "restored": [], "errors": [], "reboot_required": False})
            return 0
        res = undo_host(state, root)
        _emit(res)
        return 0 if res["ok"] else 1
    elif cmd == "verify":
        _emit(verify_host(payload["target"], root))
    elif cmd == "mark-reboot":
        state = load_state(root) or new_state()
        goto_step(state, 5)
        state["reboot_needed"] = True
        state["boot_id"] = boot_id(root)
        save_state(state, root)
    elif cmd == "rebooted":
        print("yes" if rebooted_since_apply(load_state(root) or new_state(), root) else "no")
    elif cmd == "reboot-pending":
        print("yes" if reboot_pending(load_state(root) or new_state(), root) else "no")
    elif cmd == "state":
        _emit(load_state(root) or new_state())
    elif cmd == "state-save":
        state = {**(load_state(root) or new_state()), **payload}
        save_state(state, root)
    elif cmd == "hint-set":
        set_login_hint(load_state(root) or new_state(), root)
    elif cmd == "hint-clear":
        clear_login_hint(root)
    else:
        sys.stderr.write("usage: host_setup.py facts|target|step|plan|preview|apply|undo|verify|state|state-save|reboot-pending|rebooted|mark-reboot|hint-set|hint-clear\n")
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
