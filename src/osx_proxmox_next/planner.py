from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shlex import join

from .assets import resolve_opencore_path, resolve_recovery_or_installer_path
from .defaults import CpuInfo, detect_cpu_info
from .domain import SUPPORTED_MACOS, VmConfig
from .infrastructure import ProxmoxAdapter
from .smbios import generate_rom_from_mac, generate_smbios, model_for_macos


@dataclass
class PlanStep:
    title: str
    argv: list[str]
    risk: str = "safe"

    @property
    def command(self) -> str:
        return join(self.argv)


def _cpu_args(cpu: CpuInfo, override: str = "") -> str:
    """Return QEMU -cpu flag tailored to host CPU.

    If *override* is provided (e.g. ``Skylake-Server-IBRS``), it is used
    directly as the -cpu model with standard KVM flags.

    AMD always uses Cascadelake-Server emulation (no native macOS support).
    Intel hybrid CPUs (12th gen+) also need Cascadelake-Server because macOS
    hardware validation fails on P+E core topology with correct SMBIOS.

    Non-hybrid Intel uses ``-cpu host`` for native passthrough.

    Ref: luchina-gabriel/OSX-PROXMOX (battle-tested on ~5k installs).
    """
    if override:
        return (
            f"-cpu {override},"
            "kvm=on,"
            "vendor=GenuineIntel,"
            "+invtsc,"
            "vmware-cpuid-freq=on"
        )
    if cpu.needs_emulated_cpu:
        return (
            "-cpu Cascadelake-Server,"
            "vendor=GenuineIntel,"
            "+invtsc,"
            "-pcid,"
            "-hle,-rtm,"
            "-avx512f,-avx512dq,-avx512cd,-avx512bw,-avx512vl,-avx512vnni,"
            "kvm=on,"
            "vmware-cpuid-freq=on"
        )
    return "-cpu host,kvm=on,vendor=GenuineIntel,+kvm_pv_unhalt,+kvm_pv_eoi,+hypervisor,+invtsc,vmware-cpuid-freq=on"


def build_plan(config: VmConfig) -> list[PlanStep]:
    meta = SUPPORTED_MACOS[config.macos]
    vmid = str(config.vmid)

    recovery_raw = resolve_recovery_or_installer_path(config)
    opencore_path = resolve_opencore_path(config.macos)
    oc_disk = opencore_path.parent / f"opencore-{config.macos}-vm{vmid}.img"

    macos_label = meta["label"]
    cpu = detect_cpu_info()
    cpu_flag = _cpu_args(cpu, override=config.cpu_model)
    # AMD needs kernel patches (AppleCpuPmCfgLock / AppleXcpmCfgLock).
    # Hybrid Intel does NOT — it only needs Cascadelake-Server emulation.
    is_amd = cpu.vendor == "AMD"

    steps = [
        PlanStep(
            title="Create VM shell",
            argv=[
                "qm", "create", vmid,
                "--name", config.name,
                "--ostype", "other",
                "--machine", "q35",
                "--bios", "ovmf",
                "--cores", str(config.cores),
                "--sockets", "1",
                "--memory", str(config.memory_mb),
                "--cpu", "host",
                "--balloon", "0",
                "--agent", "enabled=1",
                "--net0", f"vmxnet3,bridge={config.bridge},firewall=0",
            ],
        ),
        PlanStep(
            title="Apply macOS hardware profile",
            argv=[
                "qm", "set", vmid,
                "--args",
                '-device isa-applesmc,osk="ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc" '
                "-smbios type=2 -device qemu-xhci -device usb-kbd -device usb-tablet "
                "-global nec-usb-xhci.msi=off -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off "
                f"{cpu_flag}",
                "--vga", "std",
                "--tablet", "1",
                "--scsihw", "virtio-scsi-pci",
            ],
        ),
        *_smbios_steps(config, vmid),
        *_apple_services_steps(config, vmid),
        PlanStep(
            title="Attach EFI + TPM",
            argv=[
                "qm", "set", vmid,
                "--efidisk0", f"{config.storage}:0,efitype=4m,pre-enrolled-keys=0",
                "--tpmstate0", f"{config.storage}:0,version=v2.0",
            ],
        ),
        PlanStep(
            title="Create main disk",
            argv=["qm", "set", vmid, "--virtio0", f"{config.storage}:{config.disk_gb}"],
        ),
        PlanStep(
            title="Build OpenCore boot disk",
            argv=[
                "bash", "-c",
                _build_oc_disk_script(
                    opencore_path, recovery_raw, oc_disk, config.macos,
                    is_amd, config.cores, config.verbose_boot,
                    apple_services=config.apple_services,
                    smbios_serial=config.smbios_serial,
                    smbios_uuid=config.smbios_uuid,
                    smbios_mlb=config.smbios_mlb,
                    smbios_rom=config.smbios_rom,
                    smbios_model=config.smbios_model,
                ),
            ],
        ),
        PlanStep(
            title="Import and attach OpenCore disk",
            argv=[
                "bash", "-c",
                "if qm disk import --help >/dev/null 2>&1; then IMPORT_CMD='qm disk import'; else IMPORT_CMD='qm importdisk'; fi && "
                f"REF=$($IMPORT_CMD {vmid} {oc_disk} {config.storage} 2>&1 | "
                "grep 'successfully imported' | grep -oP \"'\\K[^']+\") && "
                f"qm set {vmid} --ide0 $REF,media=disk && "
                # Fix GPT header corruption from thin-provisioned LVM importdisk
                "DEV=$(pvesm path $REF) && "
                f"dd if={oc_disk} of=$DEV bs=512 count=2048 conv=notrunc 2>/dev/null",
            ],
        ),
        PlanStep(
            title="Stamp recovery with Apple icon flavour",
            argv=[
                "bash", "-c",
                # Fix HFS+ dirty/lock flags so Linux mounts read-write,
                # then write OpenCore .contentFlavour + .contentDetails
                "python3 -c '"
                "import struct,subprocess; "
                f"img=\"{recovery_raw}\"; "
                "out=subprocess.check_output([\"sgdisk\",\"-i\",\"1\",img],text=True); "
                "start=int([l for l in out.splitlines() if \"First sector\" in l][0].split(\":\")[1].split(\"(\")[0].strip()); "
                "off=start*512+1024+4; "
                "f=open(img,\"r+b\"); f.seek(off); "
                "a=struct.unpack(\">I\",f.read(4))[0]; "
                "a=(a|0x100)&~0x800; "
                "f.seek(off); f.write(struct.pack(\">I\",a)); "
                "f.close(); print(\"HFS+ flags fixed\")' && "
                # Cleanup stale loops from previous failed runs
                f"for lo in $(losetup -j {recovery_raw} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; "
                f"RLOOP=$(losetup -fP --show {recovery_raw}) && "
                "{ [ -b \"$RLOOP\" ] || { echo 'ERROR: losetup failed for recovery image. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
                # Retry partprobe up to 5 times for slow storage (partprobe first, then check)
                "partprobe $RLOOP 2>/dev/null; "
                "for _i in 1 2 3 4 5; do ls ${RLOOP}p* &>/dev/null && break; sleep 1; partprobe $RLOOP 2>/dev/null; done && "
                "{ [ -b \"${RLOOP}p1\" ] || { echo \"ERROR: ${RLOOP}p1 not found after partprobe. Hint: Try running the script again (slow storage)\"; false; }; } && "
                "mkdir -p /tmp/oc-recovery && "
                "mount -t hfsplus -o rw ${RLOOP}p1 /tmp/oc-recovery && "
                "{ mountpoint -q /tmp/oc-recovery || { echo \"ERROR: /tmp/oc-recovery is not mounted. Hints: file ${RLOOP}p1; blkid ${RLOOP}p1; dmesg | tail -5\"; false; }; } && "
                # Set custom name via .contentDetails in blessed directory
                "mkdir -p /tmp/oc-recovery/System/Library/CoreServices && "
                "rm -f /tmp/oc-recovery/System/Library/CoreServices/.contentDetails 2>/dev/null; "
                f"printf '{macos_label}' > /tmp/oc-recovery/System/Library/CoreServices/.contentDetails && "
                # Copy macOS installer icon as .VolumeIcon.icns for boot picker
                "ICON=$(find /tmp/oc-recovery -path '*/Install macOS*/Contents/Resources/InstallAssistant.icns' 2>/dev/null | head -1) && "
                "if [ -n \"$ICON\" ]; then "
                "rm -f /tmp/oc-recovery/.VolumeIcon.icns; "
                "cp \"$ICON\" /tmp/oc-recovery/.VolumeIcon.icns && "
                "echo \"Volume icon set from $ICON\"; "
                "else echo \"No InstallAssistant.icns found, using default icon\"; fi && "
                "{ umount /tmp/oc-recovery || umount -l /tmp/oc-recovery; } && losetup -d $RLOOP",
            ],
        ),
        PlanStep(
            title="Import and attach macOS recovery",
            argv=[
                "bash", "-c",
                "if qm disk import --help >/dev/null 2>&1; then IMPORT_CMD='qm disk import'; else IMPORT_CMD='qm importdisk'; fi && "
                f"REF=$($IMPORT_CMD {vmid} {recovery_raw} {config.storage} 2>&1 | "
                "grep 'successfully imported' | grep -oP \"'\\K[^']+\") && "
                f"qm set {vmid} --ide2 $REF,media=disk",
            ],
        ),
        PlanStep(
            title="Set boot order",
            argv=["qm", "set", vmid, "--boot", "order=ide2;virtio0;ide0"],
        ),
        PlanStep(
            title="Start VM",
            argv=["qm", "start", vmid],
            risk="action",
        ),
    ]

    if meta["channel"] == "preview":
        steps.insert(
            0,
            PlanStep(
                title="Preview warning",
                argv=[
                    "echo",
                    f"Notice: {meta['label']} uses preview assets. Verify OpenCore and recovery sources before production use.",
                ],
                risk="warn",
            ),
        )
    return steps


def render_script(config: VmConfig, steps: list[PlanStep]) -> str:
    meta = SUPPORTED_MACOS[config.macos]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Generated by osx-proxmox-next on {now}",
        f"# Target: {meta['label']} (channel={meta['channel']})",
        f"# VMID: {config.vmid}",
        "",
    ]
    for idx, step in enumerate(steps, start=1):
        lines.append(f"echo '[{idx}/{len(steps)}] {step.title}'")
        lines.append(step.command)
        lines.append("")
    return "\n".join(lines)


def _build_oc_disk_script(
    opencore_path: Path, recovery_path: Path, dest: Path, macos: str,
    is_amd: bool = False, cores: int = 4, verbose_boot: bool = False,
    apple_services: bool = False, smbios_serial: str = "",
    smbios_uuid: str = "", smbios_mlb: str = "", smbios_rom: str = "",
    smbios_model: str = "",
) -> str:
    """Build a bash script that creates a GPT+ESP OpenCore disk with patched config."""
    meta = SUPPORTED_MACOS.get(macos, {})
    macos_label = meta.get("label", f"macOS {macos.title()}")

    # AMD VM config — follows luchina-gabriel/OSX-PROXMOX's proven approach:
    # Cascadelake-Server handles CPUID emulation, only minimal PENRYN kernel
    # patches are needed (not the full AMD_Vanilla set which is for bare-metal).
    # SecureBootModel=Disabled + DmgLoading=Any — required because dmg2img-converted
    # recovery images are not Apple-signed. SecureBootModel must be Disabled
    # when DmgLoading=Any (OpenCore enforces this constraint).
    amd_patch_block = ""
    if is_amd:
        amd_patch_block = (
            # Flip power management locks for AMD
            "kq=p[\"Kernel\"][\"Quirks\"]; "
            "kq[\"AppleCpuPmCfgLock\"]=True; "
            "kq[\"AppleXcpmCfgLock\"]=True; "
        )

    # PlatformInfo — required for Apple Services (iMessage, FaceTime, iCloud).
    # macOS reads identity from OpenCore's EFI PlatformInfo, not QEMU SMBIOS.
    # ROM must match NIC MAC (macOS cross-checks during Apple ID validation).
    platforminfo_block = ""
    if apple_services and smbios_serial:
        platforminfo_block = (
            "pi=p.setdefault(\"PlatformInfo\",{}).setdefault(\"Generic\",{}); "
            f"pi[\"SystemSerialNumber\"]=\"{smbios_serial}\"; "
            f"pi[\"SystemProductName\"]=\"{smbios_model}\"; "
            f"pi[\"SystemUUID\"]=\"{smbios_uuid}\"; "
            f"pi[\"MLB\"]=\"{smbios_mlb}\"; "
            f"pi[\"ROM\"]=bytes.fromhex(\"{smbios_rom}\"); "
            "p[\"PlatformInfo\"][\"UpdateSMBIOS\"]=True; "
            "p[\"PlatformInfo\"][\"UpdateDataHub\"]=True; "
        )

    return (
        # Cleanup stale mounts/loops from any previous failed run
        "umount /tmp/oc-src 2>/dev/null; umount /tmp/oc-dest 2>/dev/null; "
        f"for lo in $(losetup -j {opencore_path} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; "
        f"for lo in $(losetup -j {dest} -O NAME --noheadings 2>/dev/null); do umount -l $lo* 2>/dev/null; losetup -d $lo 2>/dev/null; done; "
        # Create 1GB GPT disk with EFI System Partition
        f"dd if=/dev/zero of={dest} bs=1M count=1024 && "
        f"sgdisk -Z {dest} && "
        f"sgdisk -n 1:0:0 -t 1:EF00 -c 1:OPENCORE {dest} && "
        # Mount source OpenCore — detect FAT32 partition by filesystem type, not position.
        f"SRC_LOOP=$(losetup -fP --show {opencore_path}) && "
        "{ [ -b \"$SRC_LOOP\" ] || { echo 'ERROR: losetup failed for OpenCore source ISO. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
        # Retry partprobe up to 5 times for slow storage (partprobe first, then check)
        "partprobe $SRC_LOOP 2>/dev/null; "
        "for _i in 1 2 3 4 5; do ls ${SRC_LOOP}p* &>/dev/null && break; sleep 1; partprobe $SRC_LOOP 2>/dev/null; done && "
        "mkdir -p /tmp/oc-src && "
        "SRC_PART=$(blkid -o device $SRC_LOOP ${SRC_LOOP}p* 2>/dev/null "
        "| xargs -I{} sh -c 'blkid -s TYPE -o value {} 2>/dev/null | grep -q vfat && echo {}' "
        "| head -1); "
        "if [ -n \"$SRC_PART\" ]; then mount \"$SRC_PART\" /tmp/oc-src; "
        "else echo 'WARN: No vfat partition found on source ISO via blkid, trying raw mount'; mount $SRC_LOOP /tmp/oc-src; fi && "
        "{ mountpoint -q /tmp/oc-src || { echo \"ERROR: /tmp/oc-src is not mounted. Hints: file $SRC_LOOP; blkid $SRC_LOOP; dmesg | tail -5\"; false; }; } && "
        # Format and mount dest ESP — label the volume OPENCORE
        f"DEST_LOOP=$(losetup -fP --show {dest}) && "
        "{ [ -b \"$DEST_LOOP\" ] || { echo 'ERROR: losetup failed for OpenCore destination disk. Hints: modprobe loop; losetup -a; ls /dev/loop*'; false; }; } && "
        "partprobe $DEST_LOOP 2>/dev/null; "
        "for _i in 1 2 3 4 5; do ls ${DEST_LOOP}p* &>/dev/null && break; sleep 1; partprobe $DEST_LOOP 2>/dev/null; done && "
        "{ [ -b \"${DEST_LOOP}p1\" ] || { echo \"ERROR: ${DEST_LOOP}p1 not found after partprobe. Hint: Try running the script again (slow storage)\"; false; }; } && "
        "mkfs.fat -F 32 -n OPENCORE ${DEST_LOOP}p1 && "
        "mkdir -p /tmp/oc-dest && mount ${DEST_LOOP}p1 /tmp/oc-dest && "
        "{ mountpoint -q /tmp/oc-dest || { echo \"ERROR: /tmp/oc-dest is not mounted. Hints: file ${DEST_LOOP}p1; blkid ${DEST_LOOP}p1; dmesg | tail -5\"; false; }; } && "
        # Copy OpenCore files (including hidden files)
        "cp -a /tmp/oc-src/. /tmp/oc-dest/ && "
        # Validate EFI structure was copied
        "{ [ -d /tmp/oc-dest/EFI/OC ] || { echo 'ERROR: OpenCore ISO does not contain expected EFI/OC directory. ISO may be corrupt.'; false; }; } && "
        # Patch config.plist: security, boot labels, hide auxiliary entries
        "python3 -c '"
        "import plistlib; "
        "f=open(\"/tmp/oc-dest/EFI/OC/config.plist\",\"rb\"); p=plistlib.load(f); f.close(); "
        "p[\"Misc\"][\"Security\"][\"ScanPolicy\"]=0; "
        "p[\"Misc\"][\"Security\"][\"DmgLoading\"]=\"Any\"; "
        "p[\"Misc\"][\"Security\"][\"SecureBootModel\"]=\"Disabled\"; "
        "p[\"Misc\"][\"Boot\"][\"Timeout\"]=15; "
        "p[\"Misc\"][\"Boot\"][\"PickerAttributes\"]=17; "
        "p[\"Misc\"][\"Boot\"][\"HideAuxiliary\"]=True; "
        "p[\"Misc\"][\"Boot\"][\"PickerMode\"]=\"External\"; "
        "p[\"Misc\"][\"Boot\"][\"PickerVariant\"]=\"Acidanthera\\\\Syrah\"; "
        "p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"csr-active-config\"]=b\"\\x67\\x0f\\x00\\x00\"; "
        f"p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"boot-args\"]=\"keepsyms=1 debug=0x100{' -v' if verbose_boot else ''}\"; "
        "p[\"NVRAM\"][\"Add\"][\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"][\"prev-lang:kbd\"]=\"en-US:0\".encode(); "
        # Ensure NVRAM Delete purges stale values so our Add entries take effect
        "nv_del=p.setdefault(\"NVRAM\",{}).setdefault(\"Delete\",{}); "
        "nv_del[\"7C436110-AB2A-4BBB-A880-FE41995C9F82\"]=[\"csr-active-config\",\"boot-args\",\"prev-lang:kbd\"]; "
        "p[\"NVRAM\"][\"WriteFlash\"]=True; "
        # Enable VirtualSMC — shipped OC ISO has it disabled
        "[k.update(Enabled=True) for k in p.get(\"Kernel\",{}).get(\"Add\",[]) if \"VirtualSMC\" in k.get(\"BundlePath\",\"\")]; "
        + amd_patch_block
        + platforminfo_block +
        "f=open(\"/tmp/oc-dest/EFI/OC/config.plist\",\"wb\"); plistlib.dump(p,f); f.close(); "
        "print(\"config.plist patched\")' && "
        # Hide OC partition from boot picker (shown only when user presses Space)
        "echo Auxiliary > /tmp/oc-dest/.contentVisibility && "
        # Cleanup mounts (lazy unmount fallback for busy mounts)
        "{ umount /tmp/oc-src || umount -l /tmp/oc-src; } && losetup -d $SRC_LOOP && "
        "{ umount /tmp/oc-dest || umount -l /tmp/oc-dest; } && losetup -d $DEST_LOOP"
    )


def _encode_smbios_value(value: str) -> str:
    """Base64-encode a value for Proxmox smbios1 fields."""
    import base64
    return base64.b64encode(value.encode()).decode()


def _smbios_steps(config: VmConfig, vmid: str) -> list[PlanStep]:
    if config.no_smbios:
        return []
    serial = config.smbios_serial
    smbios_uuid = config.smbios_uuid
    model = config.smbios_model
    if not serial:
        identity = generate_smbios(config.macos, config.apple_services)
        serial = identity.serial
        smbios_uuid = identity.uuid
        model = identity.model
        config.smbios_serial = serial
        config.smbios_uuid = smbios_uuid
        config.smbios_model = model
        config.smbios_mlb = identity.mlb
        config.smbios_rom = identity.rom
        # Propagate MAC so _apple_services_steps doesn't generate a second one
        if identity.mac and not config.static_mac:
            config.static_mac = identity.mac
    if not model:
        model = model_for_macos(config.macos)
    smbios_value = (
        f"uuid={smbios_uuid},"
        f"base64=1,"
        f"serial={_encode_smbios_value(serial)},"
        f"manufacturer={_encode_smbios_value('Apple Inc.')},"
        f"product={_encode_smbios_value(model)},"
        f"family={_encode_smbios_value('Mac')}"
    )
    return [
        PlanStep(
            title="Set SMBIOS identity",
            argv=["qm", "set", vmid, "--smbios1", smbios_value],
        ),
    ]


def _apple_services_steps(config: VmConfig, vmid: str) -> list[PlanStep]:
    """Configure vmgenid and static MAC for Apple services (iMessage, FaceTime, etc.)."""
    if not config.apple_services:
        return []

    steps = []

    # Generate vmgenid if not set
    if not config.vmgenid:
        from .smbios import generate_vmgenid
        config.vmgenid = generate_vmgenid()

    # Generate static MAC if not set
    if not config.static_mac:
        from .smbios import generate_mac
        config.static_mac = generate_mac()

    # Derive ROM from MAC — macOS cross-checks ROM against NIC during Apple ID validation
    config.smbios_rom = generate_rom_from_mac(config.static_mac)

    # Add vmgenid for Apple services
    steps.append(PlanStep(
        title="Configure vmgenid for Apple services",
        argv=["qm", "set", vmid, "--vmgenid", config.vmgenid],
    ))

    # Get current net0 config and update with static MAC
    # We'll replace the existing net0 with a static MAC
    steps.append(PlanStep(
        title="Configure static MAC for Apple services",
        argv=["qm", "set", vmid, "--net0", f"vmxnet3,bridge={config.bridge},macaddr={config.static_mac},firewall=0"],
    ))

    return steps


# ── VM Destroy ──────────────────────────────────────────────────────


@dataclass
class VmInfo:
    vmid: int
    name: str
    status: str  # "running" | "stopped"
    config_raw: str


def fetch_vm_info(vmid: int, adapter: ProxmoxAdapter | None = None) -> VmInfo | None:
    runtime = adapter or ProxmoxAdapter()
    status_result = runtime.run(["qm", "status", str(vmid)])
    if not status_result.ok:
        return None
    # Parse status line like "status: running" or "status: stopped"
    status = "stopped"
    for line in status_result.output.splitlines():
        if "running" in line.lower():
            status = "running"
            break
    config_result = runtime.run(["qm", "config", str(vmid)])
    config_raw = config_result.output if config_result.ok else ""
    # Parse name from config
    name = ""
    for line in config_raw.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    return VmInfo(vmid=vmid, name=name, status=status, config_raw=config_raw)


def build_destroy_plan(vmid: int, purge: bool = False) -> list[PlanStep]:
    vid = str(vmid)
    destroy_argv = ["qm", "destroy", vid]
    if purge:
        destroy_argv.append("--purge")
    return [
        PlanStep(title="Stop VM", argv=["qm", "stop", vid], risk="warn"),
        PlanStep(title="Destroy VM", argv=destroy_argv, risk="warn"),
    ]
