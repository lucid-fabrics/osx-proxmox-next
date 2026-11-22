"""The runner VM's cloud-init files for the macOS 27 arm64 (beta) target.

Two files: a cloud-config (vendor data, so Proxmox's own user and key settings still apply) and the setup script it
writes. The script only fetches the arm64/ tree, installs what `snapintosh build --deps --apt` lists and runs
`snapintosh`; the knowledge of how to build lives there, not here. No password and no key is written into either file."""

from __future__ import annotations

from shlex import quote

from .arm64 import RUNNER_USER, Arm64Config

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
  {{ fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile; }} || echo "no swap file: continuing without"
fi

say installing-packages
export DEBIAN_FRONTEND=noninteractive
apt-get update
packages=$({snap} build --deps --apt)
[ -n "$packages" ] || {{ echo "snapintosh listed no packages" >&2; exit 1; }}
apt-get install -y --no-install-recommends $packages
as_user 'command -v rustup >/dev/null || {{ set -o pipefail; curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal; }}'

say building
as_user {envs}{snap} build -j "$(nproc)"
as_user {snap} build --only mesa

say installing-macos
rc=0
as_user {snap} install || rc=$?
case $rc in
  0) say ready ;;
  3) say install-pending ;;
  *) exit "$rc" ;;
esac
"""


def _renderer_env(config: Arm64Config) -> str:
    """`env RENDERER_URL=... RENDERER_SHA256=... ` when the build reads the renderer from somewhere else, else nothing."""
    if not config.renderer_url:
        return ""
    return f"env {quote('RENDERER_URL=' + config.renderer_url)} {quote('RENDERER_SHA256=' + config.renderer_sha256)} "


def render_setup_script(config: Arm64Config) -> str:
    return _SETUP.format(status=quote(STATUS_PATH), log=quote(LOG_PATH), install=quote(INSTALL_DIR),
                         url=quote(config.archive_url), sha=quote(config.source_sha256), user=RUNNER_USER,
                         envs=_renderer_env(config),
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
