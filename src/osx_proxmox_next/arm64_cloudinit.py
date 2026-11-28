"""The runner VM's cloud-init files for the macOS 27 arm64 (beta) target.

Two files: a cloud-config (vendor data, so Proxmox's own user and key settings still apply) and the setup script it
writes. The script only fetches the arm64/ tree, installs what `snapintosh build --deps --apt` lists and runs
`snapintosh`; the knowledge of how to build lives there, not here. No password and no key is written into either file."""

from __future__ import annotations

from shlex import quote

from .arm64 import (
    INSTALL_AUX,
    INSTALL_DISK,
    RUNNER_USER,
    UPLOAD_AUX,
    UPLOAD_BUNDLE,
    UPLOAD_CACHE,
    UPLOAD_IPSW,
    UPLOAD_KDK,
    UPLOAD_RAMDISK,
    UPLOAD_RESTORE_AMBROSIA,
    UPLOAD_PASSWORD,
    UPLOAD_TEMPLATE,
    Arm64Config,
)

__all__ = ["INSTALL_DIR", "LOG_PATH", "STATUS_PATH", "render_cloud_init", "render_setup_script"]

INSTALL_DIR = "/opt/osx-next-arm64"
LOG_PATH = "/var/log/osx-next-arm64.log"
STATUS_PATH = "/var/lib/osx-next-arm64/status"
SETUP_PATH = "/usr/local/sbin/osx-next-arm64-setup"

_SETUP = """\
#!/bin/bash
# Written by osx-proxmox-next for the macOS 27 arm64 (beta) runner. Progress: {status}, log: {log}.
set -Eeuo pipefail
export HOME=/root   # systemd-run starts this without one, and snapintosh refuses to run without it
exec >>{log} 2>&1
mkdir -p "$(dirname {status})"
say() {{ echo "$1" > {status}; echo "== $1"; }}
trap 'say "failed: $BASH_COMMAND"' ERR

as_user() {{ runuser -l {user} -c "$*"; }}
snap_home=/home/{user}/.local/share/snapintosh

say fetching-source
mkdir -p {install}
archive=$(mktemp)
curl -fL -sS --retry 3 -o "$archive" {url}
echo {sha}"  $archive" | sha256sum -c - || {{ echo "the source archive does not match its checksum, nothing was unpacked" >&2; exit 1; }}
tar -xzf "$archive" -C {install} --strip-components=2 --wildcards '*/arm64/*'
rm -f "$archive"
chown -R {user}:{user} {install}

say adding-swap
# a safety net for the Rust half of the build (rustc peaks near 3.5 GB); the cloud image has no swap
if ! swapon --show --noheadings | grep -q .; then
  {{ fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null \
    && swapon /swapfile; }} || echo "no swap file: continuing without"
fi
# a second file is the safety net the restore needs: 6 GiB guest, m2bhost and the QEMU stack on a 10 GiB runner
if [ ! -e /swap2 ]; then
  {{ fallocate -l 6G /swap2 && chmod 600 /swap2 && mkswap /swap2 >/dev/null && swapon /swap2; }} \
    || echo "no second swap file: continuing with the first"
fi

say installing-packages
export DEBIAN_FRONTEND=noninteractive
apt-get update
packages=$({snap} build --deps --apt)
[ -n "$packages" ] || {{ echo "snapintosh listed no packages" >&2; exit 1; }}
apt-get install -y --no-install-recommends $packages
as_user 'command -v rustup >/dev/null || {{ set -o pipefail; curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal; }}'

say building
as_user {envs}{snap} build --m2b -j "$(nproc)"
as_user {snap} build --only mesa

# The inputs the Proxmox host uploads can arrive after the build. Wait for them, but only for a bounded time, then say
# which file never showed up. The installer and firstboot paths below are absolute and independent of the caller.
firstboot_aux={aux}
firstboot_ipsw={ipsw}
firstboot_kdk={kdk}
firstboot_template={template}
firstboot_password={password}
restore_bundle={bundle}
restore_ramdisk={ramdisk}
restore_ambrosia={ambrosia}
restore_cache={cache}
wait_for() {{
  local f=$1 what=$2 i=0 limit=${{SNAPINTOSH_INPUT_WAIT:-1800}}
  while [ ! -e "$f" ]; do
    [ "$i" -lt "$limit" ] || {{ echo "no $what at $f after $limit s" >&2; exit 1; }}
    sleep 5; i=$((i + 5))
  done
}}
say waiting-for-inputs
wait_for "$firstboot_aux" "aux image"
[ -z "$firstboot_ipsw" ] || wait_for "$firstboot_ipsw" "IPSW"
[ -z "$firstboot_kdk" ] || wait_for "$firstboot_kdk" "KDK directory"
[ -z "$firstboot_template" ] || wait_for "$firstboot_template" "policy template"
[ -z "$firstboot_password" ] || wait_for "$firstboot_password" "password file"
[ -z "$restore_bundle" ] || wait_for "$restore_bundle" "restore bundle"
[ -z "$restore_ramdisk" ] || wait_for "$restore_ramdisk" "restore ramdisk"
[ -z "$restore_ambrosia" ] || wait_for "$restore_ambrosia" "restore Ambrosia"
[ -z "$restore_cache" ] || wait_for "$restore_cache" "TSS cache"
if [ -z "$firstboot_ipsw" ] && ! compgen -G "$snap_home/dl/*.ipsw" >/dev/null; then
  say fetching-ipsw
  as_user '{snap} fetch --build 26B131'
fi

say installing-macos
rc=0
as_user env RAM=6G CPUS=12 BUNDLE="$restore_bundle" RAMDISK="$restore_ramdisk" AMBROSIA="$restore_ambrosia" \
  {snap} install{install_args}{cache_arg} || rc=$?
case $rc in
  0|3) ;;
  *) exit "$rc" ;;
esac
if [ -n "$firstboot_kdk" ]; then
  say firstboot
  as_user {snap} firstboot{firstboot_args}
  say ready
else
  say installed
fi
"""


def _renderer_env(config: Arm64Config) -> str:
    """`env RENDERER_URL=... RENDERER_SHA256=... ` when the build reads the renderer from somewhere else, else nothing."""
    if not config.renderer_url:
        return ""
    return f"env {quote('RENDERER_URL=' + config.renderer_url)} {quote('RENDERER_SHA256=' + config.renderer_sha256)} "


def runner_aux(config: Arm64Config) -> str:
    """The aux image the install and firstboot read on the runner: the uploaded one wins, else the runner path."""
    if config.aux_file:
        return UPLOAD_AUX
    return config.aux or INSTALL_AUX


def runner_disk(config: Arm64Config) -> str:
    return config.disk_target or INSTALL_DISK


def runner_ipsw(config: Arm64Config) -> str:
    return UPLOAD_IPSW if config.ipsw_file else config.ipsw


def _install_args(config: Arm64Config) -> str:
    """The Mac-free install's inputs as arguments, plus --tss so Apple's TSS answers are allowed."""
    parts: list[str] = []
    for flag, value in (("--ipsw", runner_ipsw(config)), ("--aux", runner_aux(config) if config.aux_file or config.aux else ""),
                        ("--disk", config.disk_target)):
        if value:
            parts.extend([flag, quote(value)])
    if config.ecid:
        parts.extend(["--ecid", quote(config.ecid)])
    parts.append("--tss")
    return " " + " ".join(parts)


def firstboot_args(config: Arm64Config) -> str:
    """The firstboot command after a successful restore, or "" when the local Apple inputs were not supplied."""
    if not (config.kdk_dir and config.template_file):
        return ""
    parts = ["--disk", quote(runner_disk(config)), "--aux", quote(runner_aux(config)),
             "--ecid", quote(config.ecid or "0"), "--kdk", quote(UPLOAD_KDK), "--template", quote(UPLOAD_TEMPLATE),
             "--user", quote(config.guest_user)]
    if config.password_file:
        parts.extend(["--password-file", quote(UPLOAD_PASSWORD)])
    return " " + " ".join(parts)


def render_setup_script(config: Arm64Config) -> str:
    aux = UPLOAD_AUX if config.aux_file else ""
    ipsw = runner_ipsw(config)
    kdk = UPLOAD_KDK if config.kdk_dir else ""
    template = UPLOAD_TEMPLATE if config.template_file else ""
    password = UPLOAD_PASSWORD if config.password_file else ""
    bundle = UPLOAD_BUNDLE if config.bundle_file else ""
    ramdisk = UPLOAD_RAMDISK if config.ramdisk_file else ""
    ambrosia = UPLOAD_RESTORE_AMBROSIA if config.restore_ambrosia_file else ""
    cache = UPLOAD_CACHE if config.cache_dir else ""
    cache_arg = " --cache " + quote(UPLOAD_CACHE) if config.cache_dir else ""
    return _SETUP.format(status=quote(STATUS_PATH), log=quote(LOG_PATH), install=quote(INSTALL_DIR),
                         url=quote(config.archive_url), sha=quote(config.source_sha256), user=RUNNER_USER,
                         envs=_renderer_env(config), install_args=_install_args(config),
                         firstboot_args=firstboot_args(config), aux=quote(aux or runner_aux(config)), cache_arg=cache_arg,
                         ipsw=quote(ipsw), kdk=quote(kdk), template=quote(template), password=quote(password),
                         bundle=quote(bundle), ramdisk=quote(ramdisk), ambrosia=quote(ambrosia), cache=quote(cache),
                         snap=quote(f"{INSTALL_DIR}/snapintosh"))


def _indent(text: str, pad: str) -> str:
    return "".join(f"{pad}{line}\n" if line else "\n" for line in text.splitlines())


def render_cloud_init(config: Arm64Config) -> str:
    """The cloud-config that makes the runner build the beta on its first boot."""
    return (
        "#cloud-config\n"
        "package_update: false\n"
        "packages:\n"
        "  - curl\n"
        "  - ca-certificates\n"
        "  - tar\n"
        "  - qemu-guest-agent\n"
        "write_files:\n"
        f"  - path: {SETUP_PATH}\n"
        "    permissions: '0755'\n"
        "    content: |\n"
        f"{_indent(render_setup_script(config), '      ')}"
        "runcmd:\n"
        "  - [systemctl, enable, --now, qemu-guest-agent]\n"
        f"  - [systemd-run, --unit=osx-next-arm64, --no-block, {SETUP_PATH}]\n"
    )
