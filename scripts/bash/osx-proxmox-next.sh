#!/usr/bin/env bash

# Copyright (c) 2024-2026 Wassim Mehanna (lucid-fabrics)
# License: MIT | https://github.com/lucid-fabrics/osx-proxmox-next/blob/main/LICENSE

function header_info {
  clear
  cat <<"EOF"
                        ____  _____   _    ____  ___
   ____ ___  ____ _____/ __ \/ ___/  | |  / /  |/  /
  / __ `__ \/ __ `/ __/ / / /\__ \   | | / / /|_/ /
 / / / / / / /_/ / /_/ /_/ /___/ /   | |/ / /  / /
/_/ /_/ /_/\__,_/\__/\____//____/    |___/_/  /_/

EOF
}
header_info
echo -e "\n Loading..."

GEN_MAC=02:$(openssl rand -hex 5 | awk '{print toupper($0)}' | sed 's/\(..\)/\1:/g; s/.$//')
if ! [[ "$GEN_MAC" =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]]; then
  echo "ERROR: Failed to generate valid MAC address: $GEN_MAC" >&2
  exit 1
fi
APPLE_SERVICES="false"

YW=$(echo "\033[33m")
BL=$(echo "\033[36m")
RD=$(echo "\033[01;31m")
BGN=$(echo "\033[4;92m")
GN=$(echo "\033[1;92m")
DGN=$(echo "\033[32m")
CL=$(echo "\033[m")

BOLD=$(echo "\033[1m")
BFR="\\r\\033[K"
HOLD=" "
TAB="  "

CM="${TAB}✔️${TAB}${CL}"
CROSS="${TAB}✖️${TAB}${CL}"
INFO="${TAB}💡${TAB}${CL}"
OS="${TAB}🖥️${TAB}${CL}"
CONTAINERTYPE="${TAB}📦${TAB}${CL}"
DISKSIZE="${TAB}💾${TAB}${CL}"
CPUCORE="${TAB}🧠${TAB}${CL}"
RAMSIZE="${TAB}🛠️${TAB}${CL}"
CONTAINERID="${TAB}🆔${TAB}${CL}"
LBL_HOSTNAME="${TAB}🏠${TAB}${CL}"
BRIDGE="${TAB}🌉${TAB}${CL}"
GATEWAY="${TAB}🌐${TAB}${CL}"
DEFAULT="${TAB}⚙️${TAB}${CL}"
MACADDRESS="${TAB}🔗${TAB}${CL}"
VLANTAG="${TAB}🏷️${TAB}${CL}"
CREATING="${TAB}🚀${TAB}${CL}"
ADVANCED="${TAB}🧩${TAB}${CL}"

# ── macOS version definitions ──
declare -A MACOS_LABELS=(
  ["ventura"]="macOS Ventura 13"
  ["sonoma"]="macOS Sonoma 14"
  ["sequoia"]="macOS Sequoia 15"
  ["tahoe"]="macOS Tahoe 26"
)
declare -A MACOS_BOARD_IDS=(
  ["ventura"]="Mac-4B682C642B45593E"
  ["sonoma"]="Mac-827FAC58A8FDFA22"
  ["sequoia"]="Mac-27AD2F918AE68F61"
  ["tahoe"]="Mac-27AD2F918AE68F61"
)
declare -A MACOS_OS_TYPE=(
  ["ventura"]="default"
  ["sonoma"]="default"
  ["sequoia"]="default"
  ["tahoe"]="latest"
)
declare -A SMBIOS_MODELS=(
  ["ventura"]="MacPro7,1"
  ["sonoma"]="MacPro7,1"
  ["sequoia"]="MacPro7,1"
  ["tahoe"]="MacPro7,1"
)

# ── OpenCore ISO fallback URL (used only when local assembly fails) ──
OC_URL="https://github.com/lucid-fabrics/osx-proxmox-next/releases/download/assets/opencore-osx-proxmox-vm.iso"

# ── OpenCore component pins (keep in sync with src/osx_proxmox_next/oc_builder.py) ──
# The boot image is assembled locally from these upstream releases, each
# verified against its pinned SHA-256 before use.
OC_ACID="https://github.com/acidanthera"
OCBD_COMMIT="e74e533d8f89c1d5014cfb47c185502bf415741f"
declare -A OC_COMPONENT_URLS=(
  ["OpenCorePkg"]="$OC_ACID/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip"
  ["Lilu"]="$OC_ACID/Lilu/releases/download/1.7.2/Lilu-1.7.2-RELEASE.zip"
  ["VirtualSMC"]="$OC_ACID/VirtualSMC/releases/download/1.3.7/VirtualSMC-1.3.7-RELEASE.zip"
  ["WhateverGreen"]="$OC_ACID/WhateverGreen/releases/download/1.7.0/WhateverGreen-1.7.0-RELEASE.zip"
  ["AppleALC"]="$OC_ACID/AppleALC/releases/download/1.9.7/AppleALC-1.9.7-RELEASE.zip"
  ["CryptexFixup"]="$OC_ACID/CryptexFixup/releases/download/1.0.5/CryptexFixup-1.0.5-RELEASE.zip"
  ["RestrictEvents"]="$OC_ACID/RestrictEvents/releases/download/1.1.6/RestrictEvents-1.1.6-RELEASE.zip"
  ["OcBinaryData"]="$OC_ACID/OcBinaryData/archive/${OCBD_COMMIT}.tar.gz"
)
declare -A OC_COMPONENT_SHA256=(
  ["OpenCorePkg"]="2ffab6ebf58c7aefb0bcb3a1a385d207746823d6dd87d44bd666e1286939943e"
  ["Lilu"]="53967d7dcfaab01023a33df2e969a89522f13d6654a6a56ac4711b62dabf3ab8"
  ["VirtualSMC"]="12f1d379969f926306fa92d94ddbf33b32b31176589dc42089d864a26b31b700"
  ["WhateverGreen"]="6d6ffe8334ad60f784a662794e67b2560b79d757d506841dc8ca9994ab39979b"
  ["AppleALC"]="81a8ba79986130e8c845fff595950226cbc30e588f8d37089e467f776469c29d"
  ["CryptexFixup"]="25041d94a0fe9a0261caf0ba89b36dfcb21682bf3c697a34bcaddc839576ab30"
  ["RestrictEvents"]="98170dfae195ddd28b5d95e3f040125a13ca783bcb9bd1e5b8c588e217b14ee6"
  ["OcBinaryData"]="397c676372794bf0b63ac6fa5e3b1924caafad7da28f82d3ecad996eea5842ea"
)

set -euo pipefail
trap 'error_handler $LINENO "$BASH_COMMAND"' ERR
trap cleanup EXIT

function error_handler() {
  local exit_code="$?"
  local line_number="$1"
  local command="$2"
  local error_message="${RD}[ERROR]${CL} in line ${RD}$line_number${CL}: exit code ${RD}$exit_code${CL}: while executing command ${YW}$command${CL}"
  echo -e "\n$error_message\n"
  trap - ERR  # Prevent recursion if cleanup_vmid fails
  cleanup_vmid
}

function get_valid_nextid() {
  local try_id
  try_id=$(pvesh get /cluster/nextid)
  while true; do
    if [ -f "/etc/pve/qemu-server/${try_id}.conf" ] || [ -f "/etc/pve/lxc/${try_id}.conf" ]; then
      try_id=$((try_id + 1))
      continue
    fi
    if lvs --noheadings -o lv_name 2>/dev/null | grep -qE "(^|[-_])${try_id}($|[-_])"; then
      try_id=$((try_id + 1))
      continue
    fi
    break
  done
  echo "$try_id"
}

function cleanup_vmid() {
  if [ -z "${VMID:-}" ]; then return; fi
  if qm status "$VMID" &>/dev/null; then
    qm stop "$VMID" &>/dev/null
    qm destroy "$VMID" --purge &>/dev/null
  fi
}

function cleanup() {
  local mnt loop
  # Unmount all tracked mount points (lazy fallback for busy mounts)
  for mnt in "${BUILD_SRC_MNT:-}" "${BUILD_DEST_MNT:-}" "${RECOVERY_MNT:-}"; do
    [ -n "$mnt" ] && { umount "$mnt" 2>/dev/null || umount -l "$mnt" 2>/dev/null || true; }
  done
  # Detach all tracked loop devices
  for loop in "${BUILD_SRC_LOOP:-}" "${BUILD_LOOP:-}" "${RLOOP:-}"; do
    [ -n "$loop" ] && { losetup -d "$loop" 2>/dev/null || true; }
  done
  popd >/dev/null 2>/dev/null || true
  rm -rf "${TEMP_DIR:-}"
}

function msg_info() {
  local msg="$1"
  echo -ne "${TAB}${YW}${HOLD}${msg}${HOLD}"
}

function msg_ok() {
  local msg="$1"
  echo -e "${BFR}${CM}${GN}${msg}${CL}"
}

function msg_error() {
  local msg="$1"
  echo -e "${BFR}${CROSS}${RD}${msg}${CL}"
}

# ── Loop/mount helper functions ──
# Cleanup stale loop devices attached to FILE from a previous failed run.
function cleanup_stale_loops() {
  local file="$1"
  local lo
  for lo in $(losetup -j "$file" -O NAME --noheadings 2>/dev/null); do
    umount -l "$lo"* 2>/dev/null || true
    losetup -d "$lo" 2>/dev/null || true
  done
}

# Set up a loop device with validation and retry.
# Usage: setup_loop VARNAME FILE LABEL
# Sets the variable named VARNAME to the loop device path.
function setup_loop() {
  local varname="$1"
  local file="$2"
  local label="$3"
  local dev stderr_out

  if [ ! -f "$file" ]; then
    msg_error "Cannot set up ${label}: file not found: ${file}"
    exit 1
  fi

  stderr_out=$(mktemp)
  dev=$(losetup -fP --show "$file" 2>"$stderr_out") || true
  local losetup_err
  losetup_err=$(cat "$stderr_out" 2>/dev/null)
  rm -f "$stderr_out"

  if [ -z "$dev" ] || [ ! -b "$dev" ]; then
    msg_error "ERROR: losetup failed for ${label}. Output: '${dev}${losetup_err}'"
    echo -e "  Hints: modprobe loop; losetup -a; ls /dev/loop*"
    exit 1
  fi

  # Retry partprobe up to 5 times (slow storage / device-mapper lag)
  local _i
  for _i in 1 2 3 4 5; do
    partprobe "$dev" 2>/dev/null || true
    if ls "${dev}p"* &>/dev/null; then
      break
    fi
    sleep 1
  done

  printf -v "$varname" '%s' "$dev"
}

# Mount with validation.
# Usage: safe_mount SRC DEST [OPTS...]
function safe_mount() {
  local src="$1"
  local dest="$2"
  shift 2

  mount "$@" "$src" "$dest" 2>/dev/null || mount "$@" "$src" "$dest" || true

  if ! mountpoint -q "$dest"; then
    msg_error "ERROR: ${dest} is not mounted after mount command"
    echo -e "  Hints: file ${src}; blkid ${src}; dmesg | tail -5"
    exit 1
  fi
}

function check_root() {
  if [[ "$(id -u)" -ne 0 || $(ps -o comm= -p "$PPID") == "sudo" ]]; then
    clear
    msg_error "Please run this script as root."
    echo -e "\nExiting..."
    sleep 2
    exit
  fi
}

pve_check() {
  local PVE_VER
  PVE_VER="$(pveversion | awk -F'/' '{print $2}' | awk -F'-' '{print $1}')"
  # Gate on the major version only. Proxmox minor releases stay compatible,
  # and capping the minor broke every new point release (9.2 rejected, #123).
  if [[ "$PVE_VER" =~ ^(8|9)\. ]]; then
    return 0
  fi
  msg_error "Requires Proxmox VE 8.x or 9.x (detected: ${PVE_VER:-unknown})"
  exit 1
}

function arch_check() {
  if [ "$(dpkg --print-architecture)" != "amd64" ]; then
    echo -e "\n ${INFO}${YW}macOS VMs require Intel/AMD (amd64) architecture.${CL}\n"
    echo -e "Exiting..."
    sleep 2
    exit
  fi
}

function ssh_check() {
  if command -v pveversion >/dev/null 2>&1; then
    if [ -n "${SSH_CLIENT:+x}" ]; then
      if whiptail --backtitle "OSX Proxmox Next" --defaultno --title "SSH DETECTED" --yesno "It's suggested to use the Proxmox shell instead of SSH, since SSH can create issues while gathering variables. Would you like to proceed with using SSH?" 10 62; then
        echo "you've been warned"
      else
        clear
        exit
      fi
    fi
  fi
}

function exit-script() {
  clear
  echo -e "\n${CROSS}${RD}User exited script${CL}\n"
  exit
}

# ── Check required build tools ──
function check_dependencies() {
  local missing=()
  for cmd in dmg2img sgdisk partprobe losetup mkfs.fat blkid curl python3; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    msg_info "Installing missing dependencies: ${missing[*]}"
    apt-get update -qq &>/dev/null
    local pkg_map=(
      "dmg2img:dmg2img"
      "sgdisk:gdisk"
      "partprobe:parted"
      "losetup:mount"
      "mkfs.fat:dosfstools"
      "blkid:util-linux"
      "curl:curl"
      "python3:python3"
    )
    for entry in "${pkg_map[@]}"; do
      local cmd="${entry%%:*}"
      local pkg="${entry#*:}"
      if ! command -v "$cmd" &>/dev/null; then
        apt-get install -y "$pkg" &>/dev/null || {
          msg_error "Failed to install $pkg (provides $cmd)"
          echo -e "\nInstall manually: apt install $pkg"
          exit 1
        }
      fi
    done
    msg_ok "Installed dependencies"
  fi
}

# ── Make sure KVM really ignores unsupported MSR reads ──
# kvm.conf is only read when the module loads, so a node where the file was
# written after kvm was already up (and never rebooted) still runs with
# ignore_msrs=N and hangs macOS at the Apple logo. Set the live value too.
function ensure_ignore_msrs() {
  local param="/sys/module/kvm/parameters/ignore_msrs"
  if ! grep -qs "ignore_msrs=Y" /etc/modprobe.d/kvm.conf; then
    echo "options kvm ignore_msrs=Y" >>/etc/modprobe.d/kvm.conf
    msg_ok "Persisted ignore_msrs=Y in /etc/modprobe.d/kvm.conf"
  fi
  [ -f "$param" ] || return 0
  [ "$(cat "$param")" = "N" ] || return 0
  if echo Y >"$param" 2>/dev/null; then
    msg_ok "Enabled ignore_msrs on the running kvm module"
  else
    msg_error "Could not set ${param} - macOS will hang at the Apple logo"
  fi
}

# ── Detect CPU vendor and model ──
function detect_cpu_vendor() {
  if grep -q "AuthenticAMD" /proc/cpuinfo 2>/dev/null; then
    echo "AMD"
  else
    echo "Intel"
  fi
}

function detect_cpu_needs_emulation() {
  local vendor family model
  vendor=$(detect_cpu_vendor)
  if [ "$vendor" = "AMD" ]; then
    echo "yes"
    return
  fi
  # Parse cpu family and model from /proc/cpuinfo
  family=$(awk -F: '/^cpu family/{print int($2); exit}' /proc/cpuinfo 2>/dev/null)
  model=$(awk -F: '/^model\t/{print int($2); exit}' /proc/cpuinfo 2>/dev/null)
  family=${family:-0}
  model=${model:-0}
  # Intel Family 6 + known hybrid models (12th gen+) need emulation
  # Model numbers: 151=Alder Lake-S, 154=Alder Lake-P, 170=Meteor Lake,
  #   183=Raptor Lake-S, 186=Raptor Lake-P, >=190 future hybrid
  if [ "$family" -eq 6 ]; then
    case "$model" in
      151|154|170|183|186) echo "yes"; return ;;
    esac
    if [ "$model" -ge 190 ]; then
      echo "yes"
      return
    fi
  fi
  echo "no"
}

# Real Xeon E5/E7 v2-v4 HEDT chips and Xeon Scalable (Skylake-SP and later:
# Platinum/Gold/Silver/Bronze) are multi-socket-capable parts that leak their
# genuine multi-package topology through -cpu host. Combined with a
# multi-socket-capable SMBIOS (MacPro7,1), XNU's scheduler can livelock
# during heavy multithreaded I/O (the installer copy phase spins at 100%
# CPU with zero disk/network progress). A fixed emulated model avoids this.
# E5/E7 overrides match the known-working profile from
# github.com/mchiappinam/proxmox-macos; the Scalable override is the same
# idea, confirmed working on a dual-socket Cascade Lake-SP host.
function detect_cpu_xeon_hedt_model() {
  local model_name
  model_name=$(awk -F: '/^model name/{print $2; exit}' /proc/cpuinfo 2>/dev/null)
  if echo "$model_name" | grep -qE "Xeon.*E[57][ -]*[0-9]+ *v2"; then
    echo "Haswell-noTSX,model=158,stepping=3"
  elif echo "$model_name" | grep -qE "Xeon.*E[57][ -]*[0-9]+ *v[34]"; then
    echo "Broadwell-noTSX,model=158"
  elif echo "$model_name" | grep -qE "Xeon.*(Platinum|Gold|Silver|Bronze)[ -]*[0-9]{4}"; then
    echo "Skylake-Client-noTSX-IBRS"
  else
    echo ""
  fi
}

CPU_VENDOR=$(detect_cpu_vendor)
CPU_NEEDS_EMULATION=$(detect_cpu_needs_emulation)
XEON_HEDT_MODEL=$(detect_cpu_xeon_hedt_model)

# ── Per-version default disk sizes (matches Python defaults.py) ──
function default_disk_gb() {
  local ver="$1"
  case "$ver" in
    tahoe)   echo 160 ;;
    sequoia) echo 128 ;;
    sonoma)  echo 96 ;;
    *)       echo 80 ;;
  esac
}

# ── Round down to nearest power of 2 ──
function round_down_pow2() {
  local n="$1"
  local p=1
  while [ $((p * 2)) -le "$n" ]; do
    p=$((p * 2))
  done
  echo "$p"
}

# ── Detect smart CPU core count (half host, power-of-2, cap 16) ──
function detect_cpu_cores() {
  local count
  count=$(nproc 2>/dev/null || echo 4)
  local half
  if [ "$count" -ge 8 ]; then
    half=$((count / 2))
  else
    half="$count"
  fi
  # Clamp 2–16
  [ "$half" -lt 2 ] && half=2
  [ "$half" -gt 16 ] && half=16
  round_down_pow2 "$half"
}

# Kept free for the Proxmox host itself (pve daemons, ZFS ARC headroom, ssh).
# The VM runs with balloon=0, so its full allocation is pinned at qm start.
HOST_RAM_RESERVE_MB=1024

# ── Host MemAvailable in MiB (0 = unknown) ──
function detect_available_memory_mb() {
  local kb
  kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
  if ! [[ "$kb" =~ ^[0-9]+$ ]] || [ "$kb" -le 0 ]; then
    echo 0
    return
  fi
  echo $((kb / 1024))
}

# ── Largest VM allocation the host can take right now (0 = unknown) ──
function max_vm_memory_mb() {
  local avail
  avail=$(detect_available_memory_mb)
  if [ "$avail" -le 0 ]; then
    echo 0
  elif [ "$avail" -le "$HOST_RAM_RESERVE_MB" ]; then
    echo 0
  else
    echo $((avail - HOST_RAM_RESERVE_MB))
  fi
}

# ── Block when the requested RAM exceeds what the host has free ──
# balloon=0 pins the whole allocation at qm start, so more than the host's
# free RAM either fails the start or OOM-kills mid-install. Keep in sync
# with forms/form_handler.py validate_form_values.
function require_vm_memory_fits() {
  local requested="$1" limit
  limit=$(max_vm_memory_mb)
  if [ "$limit" -gt 0 ] && [ "$requested" -gt "$limit" ]; then
    msg_error "Not enough free RAM on this host for a ${requested} MiB VM"
    echo -e "  Host has only ${limit} MiB free for a VM (MemAvailable minus ${HOST_RAM_RESERVE_MB} MiB host reserve)."
    echo -e "  Lower the VM memory or free up host RAM, then run the script again."
    exit 1
  fi
}

# ── Detect smart memory default (half host, clamp 4096–32768, fit free RAM) ──
function detect_memory_mb() {
  local mem_kb
  mem_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "$mem_kb" -le 0 ] 2>/dev/null; then
    echo 8192
    return
  fi
  local mem_mb=$((mem_kb / 1024))
  local half=$((mem_mb / 2))
  # Never suggest more than what is actually free right now (balloon=0).
  local limit
  limit=$(max_vm_memory_mb)
  if [ "$limit" -gt 0 ] && [ "$half" -gt "$limit" ]; then
    half="$limit"
  fi
  [ "$half" -lt 4096 ] && half=4096
  [ "$half" -gt 32768 ] && half=32768
  echo "$half"
}

# ── Generate SMBIOS identity ──
function generate_smbios() {
  local macos_ver="$1"
  local existing_uuid="${2:-}"
  SMBIOS_MODEL="${SMBIOS_MODELS[$macos_ver]:-MacPro7,1}"

  if [ "$APPLE_SERVICES" = "true" ]; then
    # Apple-format serial+MLB via inline Python (same algorithm as smbios.py).
    # Constants are duplicated here, keep in sync with smbios.py APPLE_PLATFORM_DATA.
    # Model passed via env var to avoid shell injection into Python source.
    local smbios_out
    smbios_out=$(SMBIOS_MODEL_ENV="$SMBIOS_MODEL" python3 -c "
import os, secrets

BASE34 = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'
YEAR_CHARS = 'CDFGHJKLMN'

PLATFORMS = {
    'MacPro7,1': {
        'model_codes': ['P7QM','PLXV','PLXW','PLXX','PLXY','P7QJ','P7QK','P7QL','P7QN','P7QP','NYGV','K7GF','K7GD','N5RN'],
        'board_codes': ['K3F7'],
        'country_codes': ['C02','C07','CK2'],
        'year_range': (2019, 2023),
    },
}

BLOCK1 = ['200','600','403','404','405','303','108','207','609','501','306','102','701','301']
BLOCK2 = ['Q' + c for c in BASE34]

model = os.environ['SMBIOS_MODEL_ENV']
p = PLATFORMS[model]
yr_lo, yr_hi = p['year_range']
country = secrets.choice(p['country_codes'])
year = yr_lo + secrets.randbelow(yr_hi - yr_lo + 1)
week = 1 + secrets.randbelow(52)
line = secrets.randbelow(3400)
model_code = secrets.choice(p['model_codes'])

# Encode serial
dec = (year - 2010) % 10
if week <= 26:
    yc = YEAR_CHARS[dec]; wi = week
else:
    yc = YEAR_CHARS[(dec + 1) % 10]; wi = week - 26
d1 = line // (34 * 34); d2 = (line // 34) % 34; d3 = line % 34
serial = country + yc + BASE34[wi] + BASE34[d1] + BASE34[d2] + BASE34[d3] + model_code

# Build MLB
board = secrets.choice(p['board_codes'])
b1 = secrets.choice(BLOCK1)
b2 = secrets.choice(BLOCK2)
prefix = country + str(year % 10) + f'{week:02d}' + b1 + b2 + board
ps = sum((3 if ((i & 1) == (17 & 1)) else 1) * BASE34.index(c) for i, c in enumerate(prefix))
j16 = (-ps) % 34
mlb = prefix + '0' + BASE34[j16]

print(serial, mlb)
") || { msg_error "Failed to generate Apple-format SMBIOS"; exit 1; }
    read -r SMBIOS_SERIAL SMBIOS_MLB <<< "$smbios_out"
    if [ -z "$SMBIOS_SERIAL" ] || [ -z "$SMBIOS_MLB" ]; then
      msg_error "Apple-format SMBIOS generation returned empty values"
      exit 1
    fi
    if ! [[ "$SMBIOS_SERIAL" =~ ^[A-Z0-9]{10,14}$ ]] || ! [[ "$SMBIOS_MLB" =~ ^[A-Z0-9]{17}$ ]]; then
      msg_error "Apple-format SMBIOS output invalid: serial='$SMBIOS_SERIAL' mlb='$SMBIOS_MLB'"
      exit 1
    fi
  else
    SMBIOS_SERIAL=$(openssl rand -hex 6 | tr '[:lower:]' '[:upper:]')
    SMBIOS_MLB=$(openssl rand -hex 9 | tr '[:lower:]' '[:upper:]' | head -c 17)
  fi

  SMBIOS_ROM=$(openssl rand -hex 6 | tr '[:lower:]' '[:upper:]')
  if [ -n "$existing_uuid" ]; then
    SMBIOS_UUID="$existing_uuid"
  else
    SMBIOS_UUID=$(tr '[:lower:]' '[:upper:]' < /proc/sys/kernel/random/uuid)
  fi
}

# ── Download macOS recovery via Apple's osrecovery API ──
# Protocol reverse-engineered from OpenCorePkg macrecovery.py
function download_recovery() {
  local macos_ver="$1"
  local output_img="$2"
  local board_id="${MACOS_BOARD_IDS[$macos_ver]}"
  local os_type="${MACOS_OS_TYPE[$macos_ver]}"

  msg_info "Downloading macOS ${MACOS_LABELS[$macos_ver]} recovery image"

  local cookie_jar
  cookie_jar=$(mktemp)

  # Step 1: GET / to obtain session cookie
  curl -sS "http://osrecovery.apple.com/" \
    -H "Host: osrecovery.apple.com" \
    -H "Connection: close" \
    -H "User-Agent: InternetRecovery/1.0" \
    -c "$cookie_jar" \
    -o /dev/null 2>/dev/null || true

  # Step 2: POST to RecoveryImage with session cookie
  # cid=16 hex, k=64 hex, fg=64 hex (random each request), sn=17 zeros
  local cid k fg
  cid=$(openssl rand -hex 8 | tr '[:lower:]' '[:upper:]')
  k=$(openssl rand -hex 32 | tr '[:lower:]' '[:upper:]')
  fg=$(openssl rand -hex 32 | tr '[:lower:]' '[:upper:]')

  local post_body resp_body http_code
  resp_body=$(mktemp)
  # Body fields are newline-separated (not &-joined)
  post_body="cid=${cid}
sn=00000000000000000
bid=${board_id}
k=${k}
os=${os_type}
fg=${fg}"

  http_code=$(curl -sS -w "%{http_code}" -X POST "http://osrecovery.apple.com/InstallationPayload/RecoveryImage" \
    -H "Host: osrecovery.apple.com" \
    -H "Connection: close" \
    -H "User-Agent: InternetRecovery/1.0" \
    -H "Content-Type: text/plain" \
    -b "$cookie_jar" \
    --data-binary "$post_body" \
    -o "$resp_body" 2>/dev/null)
  rm -f "$cookie_jar"

  if [[ ! "$http_code" =~ ^2 ]]; then
    msg_error "Apple osrecovery API returned HTTP $http_code"
    msg_error "$(cat "$resp_body" 2>/dev/null)"
    rm -f "$resp_body"
    exit 1
  fi

  # Step 3: Parse response body (KEY: VALUE format, not HTTP headers)
  local image_url image_sess
  image_url=$(grep "^AU: " "$resp_body" | sed 's/^AU: //;s/\r//' | head -1)
  image_sess=$(grep "^AT: " "$resp_body" | sed 's/^AT: //;s/\r//' | head -1)
  rm -f "$resp_body"

  if [ -z "$image_url" ]; then
    msg_error "Failed to get recovery image URL from Apple"
    msg_error "This can happen if Apple's recovery servers are temporarily unavailable."
    exit 1
  fi

  # Step 4: Download BaseSystem.dmg (URL from AU: is already complete)
  # Apple's CDN can reset connections on large downloads, retry with resume
  local base_dmg="${output_img%.img}.dmg"
  local max_retries=5
  local attempt=0
  while true; do
    if curl -fSL -C - -o "$base_dmg" \
      -H "User-Agent: InternetRecovery/1.0" \
      ${image_sess:+-H "Cookie: AssetToken=${image_sess}"} \
      "$image_url"; then
      break
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_retries" ]; then
      msg_error "Failed to download BaseSystem.dmg after $max_retries attempts"
      rm -f "$base_dmg"
      exit 1
    fi
    msg_info "Download interrupted, resuming (attempt $((attempt + 1))/$max_retries)..."
    sleep 3
  done

  # Step 5: Convert DMG to raw image using dmg2img
  msg_info "Converting BaseSystem.dmg to raw disk image"
  dmg2img "$base_dmg" "$output_img" &>/dev/null || {
    msg_error "dmg2img conversion failed"
    rm -f "$base_dmg" "$output_img"
    exit 1
  }
  rm -f "$base_dmg"
  msg_ok "Downloaded and converted recovery image"
}

# ── Embedded OpenCore data files (base64; keep in sync with src/osx_proxmox_next/data/opencore/, tests diff these) ──
OC_CONFIG_TEMPLATE_B64="PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPCFET0NUWVBFIHBsaXN0IFBVQkxJQyAiLS8vQXBwbGUvL0RURCBQTElTVCAxLjAvL0VOIiAiaHR0cDovL3d3dy5hcHBsZS5jb20vRFREcy9Qcm9wZXJ0eUxpc3QtMS4wLmR0ZCI+CjxwbGlzdCB2ZXJzaW9uPSIxLjAiPgo8ZGljdD4KCTxrZXk+QUNQSTwva2V5PgoJPGRpY3Q+CgkJPGtleT5BZGQ8L2tleT4KCQk8YXJyYXk+CgkJCTxkaWN0PgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPkZha2UgRUMgYW5kIFVTQlggUG93ZXI8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5QYXRoPC9rZXk+CgkJCQk8c3RyaW5nPlNTRFQtRUMuYW1sPC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+Q1BVIEFHUE0gUGx1Z2luPTE8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5QYXRoPC9rZXk+CgkJCQk8c3RyaW5nPlNTRFQtUExVRy5hbWw8L3N0cmluZz4KCQkJPC9kaWN0PgoJCQk8ZGljdD4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5hZGQgRFRHUCBtZXRob2Q8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5QYXRoPC9rZXk+CgkJCQk8c3RyaW5nPlNTRFQtRFRHUC5hbWw8L3N0cmluZz4KCQkJPC9kaWN0PgoJCQk8ZGljdD4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5VU0IgMi4wIEluamVjdGlvbjwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PlBhdGg8L2tleT4KCQkJCTxzdHJpbmc+U1NEVC1FSENJLmFtbDwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJPC9hcnJheT4KCQk8a2V5PkRlbGV0ZTwva2V5PgoJCTxhcnJheT4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFsbDwva2V5PgoJCQkJPGZhbHNlLz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5EZWxldGUgQ3B1UG08L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPGZhbHNlLz4KCQkJCTxrZXk+T2VtVGFibGVJZDwva2V5PgoJCQkJPGRhdGE+CgkJCQlRM0IxVUcwQUFBQT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+VGFibGVMZW5ndGg8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCQk8a2V5PlRhYmxlU2lnbmF0dXJlPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCVUxTkVWQT09CgkJCQk8L2RhdGE+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFsbDwva2V5PgoJCQkJPGZhbHNlLz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5EZWxldGUgQ3B1MElzdDwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5PZW1UYWJsZUlkPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCVEzQjFNRWx6ZEFBPQoJCQkJPC9kYXRhPgoJCQkJPGtleT5UYWJsZUxlbmd0aDwva2V5PgoJCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJCTxrZXk+VGFibGVTaWduYXR1cmU8L2tleT4KCQkJCTxkYXRhPgoJCQkJVTFORVZBPT0KCQkJCTwvZGF0YT4KCQkJPC9kaWN0PgoJCTwvYXJyYXk+CgkJPGtleT5QYXRjaDwva2V5PgoJCTxhcnJheT4KCQkJPGRpY3Q+CgkJCQk8a2V5PkJhc2U8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkJhc2VTa2lwPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPlJlcGxhY2Ugb25lIGJ5dGUgc2VxdWVuY2Ugd2l0aCBhbm90aGVyPC9zdHJpbmc+CgkJCQk8a2V5PkNvdW50PC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5GaW5kPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCUVTSXpSQT09CgkJCQk8L2RhdGE+CgkJCQk8a2V5PkxpbWl0PC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5NYXNrPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCTwvZGF0YT4KCQkJCTxrZXk+T2VtVGFibGVJZDwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCQk8a2V5PlJlcGxhY2U8L2tleT4KCQkJCTxkYXRhPgoJCQkJUkRNaUVRPT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+UmVwbGFjZU1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5Ta2lwPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5UYWJsZUxlbmd0aDwva2V5PgoJCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJCTxrZXk+VGFibGVTaWduYXR1cmU8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5CYXNlPC9rZXk+CgkJCQk8c3RyaW5nPlxfU0IuUENJMC5MUENCLkhQRVQ8L3N0cmluZz4KCQkJCTxrZXk+QmFzZVNraXA8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+SFBFVCBfQ1JTIHRvIFhDUlM8L3N0cmluZz4KCQkJCTxrZXk+Q291bnQ8L2tleT4KCQkJCTxpbnRlZ2VyPjE8L2ludGVnZXI+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTxmYWxzZS8+CgkJCQk8a2V5PkZpbmQ8L2tleT4KCQkJCTxkYXRhPgoJCQkJWDBOU1V3PT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+TGltaXQ8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCQk8a2V5Pk1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5PZW1UYWJsZUlkPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCTwvZGF0YT4KCQkJCTxrZXk+UmVwbGFjZTwva2V5PgoJCQkJPGRhdGE+CgkJCQlXRU5TVXc9PQoJCQkJPC9kYXRhPgoJCQkJPGtleT5SZXBsYWNlTWFzazwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCQk8a2V5PlNraXA8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCQk8a2V5PlRhYmxlTGVuZ3RoPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5UYWJsZVNpZ25hdHVyZTwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCTwvZGljdD4KCQk8L2FycmF5PgoJCTxrZXk+UXVpcmtzPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+RmFkdEVuYWJsZVJlc2V0PC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+Tm9ybWFsaXplSGVhZGVyczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlJlYmFzZVJlZ2lvbnM8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5SZXNldEh3U2lnPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UmVzZXRMb2dvU3RhdHVzPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5TeW5jVGFibGVJZHM8L2tleT4KCQkJPGZhbHNlLz4KCQk8L2RpY3Q+Cgk8L2RpY3Q+Cgk8a2V5PkJvb3Rlcjwva2V5PgoJPGRpY3Q+CgkJPGtleT5NbWlvV2hpdGVsaXN0PC9rZXk+CgkJPGFycmF5PjwvYXJyYXk+CgkJPGtleT5QYXRjaDwva2V5PgoJCTxhcnJheT48L2FycmF5PgoJCTxrZXk+UXVpcmtzPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+QWxsb3dSZWxvY2F0aW9uQmxvY2s8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Bdm9pZFJ1bnRpbWVEZWZyYWc8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5PkNsZWFyVGFza1N3aXRjaEJpdDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkRldmlydHVhbGlzZU1taW88L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5EaXNhYmxlU2luZ2xlVXNlcjwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkRpc2FibGVWYXJpYWJsZVdyaXRlPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+RGlzY2FyZEhpYmVybmF0ZU1hcDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkVuYWJsZVNhZmVNb2RlU2xpZGU8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5PkVuYWJsZVdyaXRlVW5wcm90ZWN0b3I8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5PkZpeHVwQXBwbGVFZmlJbWFnZXM8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Gb3JjZUJvb3RlclNpZ25hdHVyZTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkZvcmNlRXhpdEJvb3RTZXJ2aWNlczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlByb3RlY3RNZW1vcnlSZWdpb25zPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UHJvdGVjdFNlY3VyZUJvb3Q8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Qcm90ZWN0VWVmaVNlcnZpY2VzPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UHJvdmlkZUN1c3RvbVNsaWRlPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5Qcm92aWRlTWF4U2xpZGU8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5SZWJ1aWxkQXBwbGVNZW1vcnlNYXA8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5SZXNpemVBcHBsZUdwdUJhcnM8L2tleT4KCQkJPGludGVnZXI+LTE8L2ludGVnZXI+CgkJCTxrZXk+U2V0dXBWaXJ0dWFsTWFwPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+U2lnbmFsQXBwbGVPUzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlN5bmNSdW50aW1lUGVybWlzc2lvbnM8L2tleT4KCQkJPGZhbHNlLz4KCQk8L2RpY3Q+Cgk8L2RpY3Q+Cgk8a2V5PkRldmljZVByb3BlcnRpZXM8L2tleT4KCTxkaWN0PgoJCTxrZXk+QWRkPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+UGNpUm9vdCgweDEpL1BjaSgweDFGLDB4MCk8L2tleT4KCQkJPGRpY3Q+CgkJCQk8a2V5PmNvbXBhdGlibGU8L2tleT4KCQkJCTxzdHJpbmc+cGNpODA4NiwyOTE2PC9zdHJpbmc+CgkJCQk8a2V5PmRldmljZS1pZDwva2V5PgoJCQkJPGRhdGE+CgkJCQlGaWtBCgkJCQk8L2RhdGE+CgkJCQk8a2V5Pm5hbWU8L2tleT4KCQkJCTxzdHJpbmc+cGNpODA4NiwyOTE2PC9zdHJpbmc+CgkJCTwvZGljdD4KCQk8L2RpY3Q+CgkJPGtleT5EZWxldGU8L2tleT4KCQk8ZGljdD48L2RpY3Q+Cgk8L2RpY3Q+Cgk8a2V5Pktlcm5lbDwva2V5PgoJPGRpY3Q+CgkJPGtleT5BZGQ8L2tleT4KCQk8YXJyYXk+CgkJCTxkaWN0PgoJCQkJPGtleT5BcmNoPC9rZXk+CgkJCQk8c3RyaW5nPkFueTwvc3RyaW5nPgoJCQkJPGtleT5CdW5kbGVQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkxpbHUua2V4dDwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPlBhdGNoIGVuZ2luZTwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkV4ZWN1dGFibGVQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkNvbnRlbnRzL01hY09TL0xpbHU8L3N0cmluZz4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+OC4wLjA8L3N0cmluZz4KCQkJCTxrZXk+UGxpc3RQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkNvbnRlbnRzL0luZm8ucGxpc3Q8L3N0cmluZz4KCQkJPC9kaWN0PgoJCQk8ZGljdD4KCQkJCTxrZXk+QXJjaDwva2V5PgoJCQkJPHN0cmluZz5Bbnk8L3N0cmluZz4KCQkJCTxrZXk+QnVuZGxlUGF0aDwva2V5PgoJCQkJPHN0cmluZz5WaXJ0dWFsU01DLmtleHQ8L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5TTUMgZW11bGF0b3I8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5FeGVjdXRhYmxlUGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9NYWNPUy9WaXJ0dWFsU01DPC9zdHJpbmc+CgkJCQk8a2V5Pk1heEtlcm5lbDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+TWluS2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjguMC4wPC9zdHJpbmc+CgkJCQk8a2V5PlBsaXN0UGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9JbmZvLnBsaXN0PC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+eDg2XzY0PC9zdHJpbmc+CgkJCQk8a2V5PkJ1bmRsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+V2hhdGV2ZXJHcmVlbi5rZXh0PC9zdHJpbmc+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+VmlkZW8gcGF0Y2hlczwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkV4ZWN1dGFibGVQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkNvbnRlbnRzL01hY09TL1doYXRldmVyR3JlZW48L3N0cmluZz4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MTAuMC4wPC9zdHJpbmc+CgkJCQk8a2V5PlBsaXN0UGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9JbmZvLnBsaXN0PC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+QW55PC9zdHJpbmc+CgkJCQk8a2V5PkJ1bmRsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+QXBwbGVBTEMua2V4dDwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPkF1ZGlvIHBhdGNoZXM8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5FeGVjdXRhYmxlUGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9NYWNPUy9BcHBsZUFMQzwvc3RyaW5nPgoJCQkJPGtleT5NYXhLZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQkJPHN0cmluZz44LjAuMDwvc3RyaW5nPgoJCQkJPGtleT5QbGlzdFBhdGg8L2tleT4KCQkJCTxzdHJpbmc+Q29udGVudHMvSW5mby5wbGlzdDwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5BcmNoPC9rZXk+CgkJCQk8c3RyaW5nPng4Nl82NDwvc3RyaW5nPgoJCQkJPGtleT5CdW5kbGVQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPk1DRVJlcG9ydGVyRGlzYWJsZXIua2V4dDwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPkFwcGxlTUNFUmVwb3J0ZXIgZGlzYWJsZXI8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5FeGVjdXRhYmxlUGF0aDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MTkuMC4wPC9zdHJpbmc+CgkJCQk8a2V5PlBsaXN0UGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9JbmZvLnBsaXN0PC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+eDg2XzY0PC9zdHJpbmc+CgkJCQk8a2V5PkJ1bmRsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+Q3J5cHRleEZpeHVwLmtleHQ8L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5TdXBwb3J0IGZvciBub24tQVZYMiBDUFVzIGluIFZlbnR1cmEvU29ub21hPC9zdHJpbmc+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTx0cnVlLz4KCQkJCTxrZXk+RXhlY3V0YWJsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+Q29udGVudHMvTWFjT1MvQ3J5cHRleEZpeHVwPC9zdHJpbmc+CgkJCQk8a2V5Pk1heEtlcm5lbDwva2V5PgoJCQkJPHN0cmluZz4yMy45OS45OTwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MjIuMS4wPC9zdHJpbmc+CgkJCQk8a2V5PlBsaXN0UGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9JbmZvLnBsaXN0PC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+QW55PC9zdHJpbmc+CgkJCQk8a2V5PkJ1bmRsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+UmVzdHJpY3RFdmVudHMua2V4dDwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPlNpbGVuY2UgTWFjUHJvNywxIG1lbW9yeSB3YXJuaW5nPC9zdHJpbmc+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTx0cnVlLz4KCQkJCTxrZXk+RXhlY3V0YWJsZVBhdGg8L2tleT4KCQkJCTxzdHJpbmc+Q29udGVudHMvTWFjT1MvUmVzdHJpY3RFdmVudHM8L3N0cmluZz4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PlBsaXN0UGF0aDwva2V5PgoJCQkJPHN0cmluZz5Db250ZW50cy9JbmZvLnBsaXN0PC9zdHJpbmc+CgkJCTwvZGljdD4KCQk8L2FycmF5PgoJCTxrZXk+QmxvY2s8L2tleT4KCQk8YXJyYXk+CgkJCTxkaWN0PgoJCQkJPGtleT5BcmNoPC9rZXk+CgkJCQk8c3RyaW5nPkFueTwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5JZGVudGlmaWVyPC9rZXk+CgkJCQk8c3RyaW5nPmNvbS5hcHBsZS5kcml2ZXIuQXBwbGVUeU1DRURyaXZlcjwvc3RyaW5nPgoJCQkJPGtleT5NYXhLZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+U3RyYXRlZ3k8L2tleT4KCQkJCTxzdHJpbmc+RGlzYWJsZTwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJPC9hcnJheT4KCQk8a2V5PkVtdWxhdGU8L2tleT4KCQk8ZGljdD4KCQkJPGtleT5DcHVpZDFEYXRhPC9rZXk+CgkJCTxkYXRhPgoJCQlWQVlGQUFBQUFBQUFBQUFBQUFBQUFBPT0KCQkJPC9kYXRhPgoJCQk8a2V5PkNwdWlkMU1hc2s8L2tleT4KCQkJPGRhdGE+CgkJCS8vLy9BQUFBQUFBQUFBQUFBQUFBQUE9PQoJCQk8L2RhdGE+CgkJCTxrZXk+RHVtbXlQb3dlck1hbmFnZW1lbnQ8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5Pk1heEtlcm5lbDwva2V5PgoJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCTwvZGljdD4KCQk8a2V5PkZvcmNlPC9rZXk+CgkJPGFycmF5PgoJCQk8ZGljdD4KCQkJCTxrZXk+QXJjaDwva2V5PgoJCQkJPHN0cmluZz5Bbnk8L3N0cmluZz4KCQkJCTxrZXk+QnVuZGxlUGF0aDwva2V5PgoJCQkJPHN0cmluZz5TeXN0ZW0vTGlicmFyeS9FeHRlbnNpb25zL0lPTmV0d29ya2luZ0ZhbWlseS5rZXh0PC9zdHJpbmc+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTxmYWxzZS8+CgkJCQk8a2V5PkV4ZWN1dGFibGVQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkNvbnRlbnRzL01hY09TL0lPTmV0d29ya2luZ0ZhbWlseTwvc3RyaW5nPgoJCQkJPGtleT5JZGVudGlmaWVyPC9rZXk+CgkJCQk8c3RyaW5nPmNvbS5hcHBsZS5pb2tpdC5JT05ldHdvcmtpbmdGYW1pbHk8L3N0cmluZz4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjEzLjk5Ljk5PC9zdHJpbmc+CgkJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+UGxpc3RQYXRoPC9rZXk+CgkJCQk8c3RyaW5nPkNvbnRlbnRzL0luZm8ucGxpc3Q8L3N0cmluZz4KCQkJPC9kaWN0PgoJCTwvYXJyYXk+CgkJPGtleT5QYXRjaDwva2V5PgoJCTxhcnJheT4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+eDg2XzY0PC9zdHJpbmc+CgkJCQk8a2V5PkJhc2U8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+YWxncmV5IC0gY3B1aWRfc2V0X2NwdWZhbWlseSAtIGZvcmNlIENQVUZBTUlMWV9JTlRFTF9QRU5SWU48L3N0cmluZz4KCQkJCTxrZXk+Q291bnQ8L2tleT4KCQkJCTxpbnRlZ2VyPjE8L2ludGVnZXI+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTx0cnVlLz4KCQkJCTxrZXk+RmluZDwva2V5PgoJCQkJPGRhdGE+CgkJCQlNZHVBUFFBQUFBQUdkUUE9CgkJCQk8L2RhdGE+CgkJCQk8a2V5PklkZW50aWZpZXI8L2tleT4KCQkJCTxzdHJpbmc+a2VybmVsPC9zdHJpbmc+CgkJCQk8a2V5PkxpbWl0PC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5NYXNrPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCS8vLy8vd0FBQVAvLy93QT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+TWF4S2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjIwLjMuOTk8L3N0cmluZz4KCQkJCTxrZXk+TWluS2VybmVsPC9rZXk+CgkJCQk8c3RyaW5nPjE3LjAuMDwvc3RyaW5nPgoJCQkJPGtleT5SZXBsYWNlPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCXU3eFA2bmpwWFFBQUFKQT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+UmVwbGFjZU1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5Ta2lwPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5BcmNoPC9rZXk+CgkJCQk8c3RyaW5nPng4Nl82NDwvc3RyaW5nPgoJCQkJPGtleT5CYXNlPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPmFsZ3JleSAtIHRoZW5pY2tkdWRlIC0gY3B1aWRfc2V0X2NwdWZhbWlseSAtIGZvcmNlIENQVUZBTUlMWV9JTlRFTF9QRU5SWU4gKEJpZyBTdXIgMTEuMyssIE1vbnRlcmV5LCBWZW50dXJhLCBTb25vbWEpPC9zdHJpbmc+CgkJCQk8a2V5PkNvdW50PC9rZXk+CgkJCQk8aW50ZWdlcj4xPC9pbnRlZ2VyPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkZpbmQ8L2tleT4KCQkJCTxkYXRhPgoJCQkJTWRLekFZQTlBQUFBQUFaMQoJCQkJPC9kYXRhPgoJCQkJPGtleT5JZGVudGlmaWVyPC9rZXk+CgkJCQk8c3RyaW5nPmtlcm5lbDwvc3RyaW5nPgoJCQkJPGtleT5MaW1pdDwva2V5PgoJCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJCTxrZXk+TWFzazwva2V5PgoJCQkJPGRhdGE+CgkJCQkvLy8vLy8vL0FBQUFBUC8vCgkJCQk8L2RhdGE+CgkJCQk8a2V5Pk1heEtlcm5lbDwva2V5PgoJCQkJPHN0cmluZz4yMy45OS45OTwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MjAuNC4wPC9zdHJpbmc+CgkJCQk8a2V5PlJlcGxhY2U8L2tleT4KCQkJCTxkYXRhPgoJCQkJdXJ4UDZuaXpBSkNRa0pEcgoJCQkJPC9kYXRhPgoJCQkJPGtleT5SZXBsYWNlTWFzazwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCQk8a2V5PlNraXA8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+eDg2XzY0PC9zdHJpbmc+CgkJCQk8a2V5PkJhc2U8L2tleT4KCQkJCTxzdHJpbmc+X2Vhcmx5X3JhbmRvbTwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPlN1clBsdXMgdjEgLSBQQVJUIDEgb2YgMiAtIFBhdGNoIHJlYWRfZXJhbmRvbSAoaW5saW5lZCBpbiBfZWFybHlfcmFuZG9tKTwvc3RyaW5nPgoJCQkJPGtleT5Db3VudDwva2V5PgoJCQkJPGludGVnZXI+MTwvaW50ZWdlcj4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5GaW5kPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCUFIUWpTSXM9CgkJCQk8L2RhdGE+CgkJCQk8a2V5PklkZW50aWZpZXI8L2tleT4KCQkJCTxzdHJpbmc+a2VybmVsPC9zdHJpbmc+CgkJCQk8a2V5PkxpbWl0PC9rZXk+CgkJCQk8aW50ZWdlcj44MDA8L2ludGVnZXI+CgkJCQk8a2V5Pk1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5NYXhLZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MjEuMS4wPC9zdHJpbmc+CgkJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQkJPHN0cmluZz4yMC40LjA8L3N0cmluZz4KCQkJCTxrZXk+UmVwbGFjZTwva2V5PgoJCQkJPGRhdGE+CgkJCQlBT3NqU0lzPQoJCQkJPC9kYXRhPgoJCQkJPGtleT5SZXBsYWNlTWFzazwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCQk8a2V5PlNraXA8L2tleT4KCQkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyY2g8L2tleT4KCQkJCTxzdHJpbmc+eDg2XzY0PC9zdHJpbmc+CgkJCQk8a2V5PkJhc2U8L2tleT4KCQkJCTxzdHJpbmc+X3JlZ2lzdGVyX2FuZF9pbml0X3Bybmc8L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5TdXJQbHVzIHYxIC0gUEFSVCAyIG9mIDIgLSBQYXRjaCByZWdpc3Rlcl9hbmRfaW5pdF9wcm5nPC9zdHJpbmc+CgkJCQk8a2V5PkNvdW50PC9rZXk+CgkJCQk8aW50ZWdlcj4xPC9pbnRlZ2VyPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkZpbmQ8L2tleT4KCQkJCTxkYXRhPgoJCQkJdWtnQkFBQXg5Zz09CgkJCQk8L2RhdGE+CgkJCQk8a2V5PklkZW50aWZpZXI8L2tleT4KCQkJCTxzdHJpbmc+a2VybmVsPC9zdHJpbmc+CgkJCQk8a2V5PkxpbWl0PC9rZXk+CgkJCQk8aW50ZWdlcj4yNTY8L2ludGVnZXI+CgkJCQk8a2V5Pk1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5NYXhLZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MjEuMS4wPC9zdHJpbmc+CgkJCQk8a2V5Pk1pbktlcm5lbDwva2V5PgoJCQkJPHN0cmluZz4yMC40LjA8L3N0cmluZz4KCQkJCTxrZXk+UmVwbGFjZTwva2V5PgoJCQkJPGRhdGE+CgkJCQl1a2dCQUFEckJRPT0KCQkJCTwvZGF0YT4KCQkJCTxrZXk+UmVwbGFjZU1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5Ta2lwPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5BcmNoPC9rZXk+CgkJCQk8c3RyaW5nPng4Nl82NDwvc3RyaW5nPgoJCQkJPGtleT5CYXNlPC9rZXk+CgkJCQk8c3RyaW5nPl9hcGZzX2ZpbGV2YXVsdF9hbGxvd2VkPC9zdHJpbmc+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+Rm9yY2UgRmlsZVZhdWx0IG9uIEJyb2tlbiBTZWFsIChmcm9tIE9DTFAgcHJvamVjdCwgZm9yIG5vbi1BVlgyIFZlbnR1cmEvU29ub21hKTwvc3RyaW5nPgoJCQkJPGtleT5Db3VudDwva2V5PgoJCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5GaW5kPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCTwvZGF0YT4KCQkJCTxrZXk+SWRlbnRpZmllcjwva2V5PgoJCQkJPHN0cmluZz5jb20uYXBwbGUuZmlsZXN5c3RlbXMuYXBmczwvc3RyaW5nPgoJCQkJPGtleT5MaW1pdDwva2V5PgoJCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJCTxrZXk+TWFzazwva2V5PgoJCQkJPGRhdGE+CgkJCQk8L2RhdGE+CgkJCQk8a2V5Pk1heEtlcm5lbDwva2V5PgoJCQkJPHN0cmluZz4yMy45OS45OTwvc3RyaW5nPgoJCQkJPGtleT5NaW5LZXJuZWw8L2tleT4KCQkJCTxzdHJpbmc+MjIuMS4wPC9zdHJpbmc+CgkJCQk8a2V5PlJlcGxhY2U8L2tleT4KCQkJCTxkYXRhPgoJCQkJdUFFQUFBREQKCQkJCTwvZGF0YT4KCQkJCTxrZXk+UmVwbGFjZU1hc2s8L2tleT4KCQkJCTxkYXRhPgoJCQkJPC9kYXRhPgoJCQkJPGtleT5Ta2lwPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8L2RpY3Q+CgkJPC9hcnJheT4KCQk8a2V5PlF1aXJrczwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkFwcGxlQ3B1UG1DZmdMb2NrPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+QXBwbGVYY3BtQ2ZnTG9jazwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlWGNwbUV4dHJhTXNyczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlWGNwbUZvcmNlQm9vc3Q8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5DdXN0b21QY2lTZXJpYWxEZXZpY2U8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5DdXN0b21TTUJJT1NHdWlkPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+RGlzYWJsZUlvTWFwcGVyPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+RGlzYWJsZUlvTWFwcGVyTWFwcGluZzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkRpc2FibGVMaW5rZWRpdEpldHRpc29uPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5EaXNhYmxlUnRjQ2hlY2tzdW08L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5FeHRlbmRCVEZlYXR1cmVGbGFnczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkV4dGVybmFsRGlza0ljb25zPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+Rm9yY2VBcXVhbnRpYUV0aGVybmV0PC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+Rm9yY2VTZWN1cmVCb290U2NoZW1lPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5JbmNyZWFzZVBjaUJhclNpemU8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5MYXBpY0tlcm5lbFBhbmljPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+TGVnYWN5Q29tbXBhZ2U8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5QYW5pY05vS2V4dER1bXA8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Qb3dlclRpbWVvdXRLZXJuZWxQYW5pYzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlByb3ZpZGVDdXJyZW50Q3B1SW5mbzwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+U2V0QXBmc1RyaW1UaW1lb3V0PC9rZXk+CgkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCTxrZXk+VGhpcmRQYXJ0eURyaXZlczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlhoY2lQb3J0TGltaXQ8L2tleT4KCQkJPGZhbHNlLz4KCQk8L2RpY3Q+CgkJPGtleT5TY2hlbWU8L2tleT4KCQk8ZGljdD4KCQkJPGtleT5DdXN0b21LZXJuZWw8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5GdXp6eU1hdGNoPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5LZXJuZWxBcmNoPC9rZXk+CgkJCTxzdHJpbmc+QXV0bzwvc3RyaW5nPgoJCQk8a2V5Pktlcm5lbENhY2hlPC9rZXk+CgkJCTxzdHJpbmc+QXV0bzwvc3RyaW5nPgoJCTwvZGljdD4KCTwvZGljdD4KCTxrZXk+TWlzYzwva2V5PgoJPGRpY3Q+CgkJPGtleT5CbGVzc092ZXJyaWRlPC9rZXk+CgkJPGFycmF5PjwvYXJyYXk+CgkJPGtleT5Cb290PC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+Q29uc29sZUF0dHJpYnV0ZXM8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5IaWJlcm5hdGVNb2RlPC9rZXk+CgkJCTxzdHJpbmc+QXV0bzwvc3RyaW5nPgoJCQk8a2V5PkhpYmVybmF0ZVNraXBzUGlja2VyPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+SGlkZUF1eGlsaWFyeTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5Pkluc3RhbmNlSWRlbnRpZmllcjwva2V5PgoJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQk8a2V5PkxhdW5jaGVyT3B0aW9uPC9rZXk+CgkJCTxzdHJpbmc+RGlzYWJsZWQ8L3N0cmluZz4KCQkJPGtleT5MYXVuY2hlclBhdGg8L2tleT4KCQkJPHN0cmluZz5EZWZhdWx0PC9zdHJpbmc+CgkJCTxrZXk+UGlja2VyQXR0cmlidXRlczwva2V5PgoJCQk8aW50ZWdlcj4xNzwvaW50ZWdlcj4KCQkJPGtleT5QaWNrZXJBdWRpb0Fzc2lzdDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlBpY2tlck1vZGU8L2tleT4KCQkJPHN0cmluZz5FeHRlcm5hbDwvc3RyaW5nPgoJCQk8a2V5PlBpY2tlclZhcmlhbnQ8L2tleT4KCQkJPHN0cmluZz5BdXRvPC9zdHJpbmc+CgkJCTxrZXk+UG9sbEFwcGxlSG90S2V5czwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+U2hvd1BpY2tlcjwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+VGFrZW9mZkRlbGF5PC9rZXk+CgkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCTxrZXk+VGltZW91dDwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCTwvZGljdD4KCQk8a2V5PkRlYnVnPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+QXBwbGVEZWJ1Zzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlUGFuaWM8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5EaXNhYmxlV2F0Y2hEb2c8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5EaXNwbGF5RGVsYXk8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5EaXNwbGF5TGV2ZWw8L2tleT4KCQkJPGludGVnZXI+MjE0NzQ4MzY1MDwvaW50ZWdlcj4KCQkJPGtleT5Mb2dNb2R1bGVzPC9rZXk+CgkJCTxzdHJpbmc+Kjwvc3RyaW5nPgoJCQk8a2V5PlN5c1JlcG9ydDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlRhcmdldDwva2V5PgoJCQk8aW50ZWdlcj4zPC9pbnRlZ2VyPgoJCTwvZGljdD4KCQk8a2V5PkVudHJpZXM8L2tleT4KCQk8YXJyYXk+PC9hcnJheT4KCQk8a2V5PlNlY3VyaXR5PC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+QWxsb3dTZXREZWZhdWx0PC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+QXBFQ0lEPC9rZXk+CgkJCTxpbnRlZ2VyPjA8L2ludGVnZXI+CgkJCTxrZXk+QXV0aFJlc3RhcnQ8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5CbGFja2xpc3RBcHBsZVVwZGF0ZTwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+RG1nTG9hZGluZzwva2V5PgoJCQk8c3RyaW5nPlNpZ25lZDwvc3RyaW5nPgoJCQk8a2V5PkVuYWJsZVBhc3N3b3JkPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+RXhwb3NlU2Vuc2l0aXZlRGF0YTwva2V5PgoJCQk8aW50ZWdlcj42PC9pbnRlZ2VyPgoJCQk8a2V5PkhhbHRMZXZlbDwva2V5PgoJCQk8aW50ZWdlcj4yMTQ3NDgzNjQ4PC9pbnRlZ2VyPgoJCQk8a2V5PlBhc3N3b3JkSGFzaDwva2V5PgoJCQk8ZGF0YT4KCQkJPC9kYXRhPgoJCQk8a2V5PlBhc3N3b3JkU2FsdDwva2V5PgoJCQk8ZGF0YT4KCQkJPC9kYXRhPgoJCQk8a2V5PlNjYW5Qb2xpY3k8L2tleT4KCQkJPGludGVnZXI+MTg4MDk2MDM8L2ludGVnZXI+CgkJCTxrZXk+U2VjdXJlQm9vdE1vZGVsPC9rZXk+CgkJCTxzdHJpbmc+RGlzYWJsZWQ8L3N0cmluZz4KCQkJPGtleT5WYXVsdDwva2V5PgoJCQk8c3RyaW5nPk9wdGlvbmFsPC9zdHJpbmc+CgkJPC9kaWN0PgoJCTxrZXk+U2VyaWFsPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+SW5pdDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5Pk92ZXJyaWRlPC9rZXk+CgkJCTxmYWxzZS8+CgkJPC9kaWN0PgoJCTxrZXk+VG9vbHM8L2tleT4KCQk8YXJyYXk+CgkJCTxkaWN0PgoJCQkJPGtleT5Bcmd1bWVudHM8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkF1eGlsaWFyeTwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPk5vdCBzaWduZWQgZm9yIHNlY3VyaXR5IHJlYXNvbnM8L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5GbGF2b3VyPC9rZXk+CgkJCQk8c3RyaW5nPk9wZW5TaGVsbDpVRUZJU2hlbGw6U2hlbGw8L3N0cmluZz4KCQkJCTxrZXk+RnVsbE52cmFtQWNjZXNzPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5OYW1lPC9rZXk+CgkJCQk8c3RyaW5nPlVFRkkgU2hlbGw8L3N0cmluZz4KCQkJCTxrZXk+UGF0aDwva2V5PgoJCQkJPHN0cmluZz5TaGVsbC5lZmk8L3N0cmluZz4KCQkJCTxrZXk+UmVhbFBhdGg8L2tleT4KCQkJCTxmYWxzZS8+CgkJCQk8a2V5PlRleHRNb2RlPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5Bcmd1bWVudHM8L2tleT4KCQkJCTxzdHJpbmc+U2h1dGRvd248L3N0cmluZz4KCQkJCTxrZXk+QXV4aWxpYXJ5PC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+UGVyZm9ybSBzaHV0ZG93bjwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5GbGF2b3VyPC9rZXk+CgkJCQk8c3RyaW5nPkF1dG88L3N0cmluZz4KCQkJCTxrZXk+RnVsbE52cmFtQWNjZXNzPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5OYW1lPC9rZXk+CgkJCQk8c3RyaW5nPlNodXRkb3duPC9zdHJpbmc+CgkJCQk8a2V5PlBhdGg8L2tleT4KCQkJCTxzdHJpbmc+UmVzZXRTeXN0ZW0uZWZpPC9zdHJpbmc+CgkJCQk8a2V5PlJlYWxQYXRoPC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5UZXh0TW9kZTwva2V5PgoJCQkJPGZhbHNlLz4KCQkJPC9kaWN0PgoJCTwvYXJyYXk+Cgk8L2RpY3Q+Cgk8a2V5Pk5WUkFNPC9rZXk+Cgk8ZGljdD4KCQk8a2V5PkFkZDwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PjREMUVERTA1LTM4QzctNEE2QS05Q0M2LTRCQ0NBOEIzOEMxNDwva2V5PgoJCQk8ZGljdD4KCQkJCTxrZXk+RGVmYXVsdEJhY2tncm91bmRDb2xvcjwva2V5PgoJCQkJPGRhdGE+CgkJCQlBQUFBQUE9PQoJCQkJPC9kYXRhPgoJCQk8L2RpY3Q+CgkJCTxrZXk+NEQxRkRBMDItMzhDNy00QTZBLTlDQzYtNEJDQ0E4QjMwMTAyPC9rZXk+CgkJCTxkaWN0PgoJCQkJPGtleT5ydGMtYmxhY2tsaXN0PC9rZXk+CgkJCQk8ZGF0YT4KCQkJCTwvZGF0YT4KCQkJPC9kaWN0PgoJCQk8a2V5PjdDNDM2MTEwLUFCMkEtNEJCQi1BODgwLUZFNDE5OTVDOUY4Mjwva2V5PgoJCQk8ZGljdD4KCQkJCTxrZXk+I0lORk8gKHByZXYtbGFuZzprYmQpPC9rZXk+CgkJCQk8c3RyaW5nPmVuOjI1MiAoQUJDKSwgc2V0IDY1NmUzYTMyMzUzMjwvc3RyaW5nPgoJCQkJPGtleT5Gb3JjZURpc3BsYXlSb3RhdGlvbkluRUZJPC9rZXk+CgkJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQkJPGtleT5TeXN0ZW1BdWRpb1ZvbHVtZTwva2V5PgoJCQkJPGRhdGE+CgkJCQlSZz09CgkJCQk8L2RhdGE+CgkJCQk8a2V5PmJvb3QtYXJnczwva2V5PgoJCQkJPHN0cmluZz5rZWVwc3ltcz0xPC9zdHJpbmc+CgkJCQk8a2V5PmNzci1hY3RpdmUtY29uZmlnPC9rZXk+CgkJCQk8ZGF0YT4KCQkJCUpnOEFBQT09CgkJCQk8L2RhdGE+CgkJCQk8a2V5PnByZXYtbGFuZzprYmQ8L2tleT4KCQkJCTxkYXRhPgoJCQkJWlc0dFZWTTZNQT09CgkJCQk8L2RhdGE+CgkJCQk8a2V5PnJ1bi1lZmktdXBkYXRlcjwva2V5PgoJCQkJPHN0cmluZz5Obzwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJPC9kaWN0PgoJCTxrZXk+RGVsZXRlPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+NEQxRURFMDUtMzhDNy00QTZBLTlDQzYtNEJDQ0E4QjM4QzE0PC9rZXk+CgkJCTxhcnJheT4KCQkJCTxzdHJpbmc+RGVmYXVsdEJhY2tncm91bmRDb2xvcjwvc3RyaW5nPgoJCQk8L2FycmF5PgoJCQk8a2V5PjREMUZEQTAyLTM4QzctNEE2QS05Q0M2LTRCQ0NBOEIzMDEwMjwva2V5PgoJCQk8YXJyYXk+CgkJCQk8c3RyaW5nPnJ0Yy1ibGFja2xpc3Q8L3N0cmluZz4KCQkJPC9hcnJheT4KCQkJPGtleT43QzQzNjExMC1BQjJBLTRCQkItQTg4MC1GRTQxOTk1QzlGODI8L2tleT4KCQkJPGFycmF5PgoJCQkJPHN0cmluZz5ib290LWFyZ3M8L3N0cmluZz4KCQkJCTxzdHJpbmc+Rm9yY2VEaXNwbGF5Um90YXRpb25JbkVGSTwvc3RyaW5nPgoJCQk8L2FycmF5PgoJCTwvZGljdD4KCQk8a2V5PkxlZ2FjeU92ZXJ3cml0ZTwva2V5PgoJCTxmYWxzZS8+CgkJPGtleT5MZWdhY3lTY2hlbWE8L2tleT4KCQk8ZGljdD4KCQkJPGtleT43QzQzNjExMC1BQjJBLTRCQkItQTg4MC1GRTQxOTk1QzlGODI8L2tleT4KCQkJPGFycmF5PgoJCQkJPHN0cmluZz5FRklMb2dpbkhpRFBJPC9zdHJpbmc+CgkJCQk8c3RyaW5nPkVGSUJsdWV0b290aERlbGF5PC9zdHJpbmc+CgkJCQk8c3RyaW5nPkxvY2F0aW9uU2VydmljZXNFbmFibGVkPC9zdHJpbmc+CgkJCQk8c3RyaW5nPlN5c3RlbUF1ZGlvVm9sdW1lPC9zdHJpbmc+CgkJCQk8c3RyaW5nPlN5c3RlbUF1ZGlvVm9sdW1lREI8L3N0cmluZz4KCQkJCTxzdHJpbmc+U3lzdGVtQXVkaW9Wb2x1bWVTYXZlZDwvc3RyaW5nPgoJCQkJPHN0cmluZz5ibHVldG9vdGhBY3RpdmVDb250cm9sbGVySW5mbzwvc3RyaW5nPgoJCQkJPHN0cmluZz5ibHVldG9vdGhJbnRlcm5hbENvbnRyb2xsZXJJbmZvPC9zdHJpbmc+CgkJCQk8c3RyaW5nPmZsYWdzdGF0ZTwvc3RyaW5nPgoJCQkJPHN0cmluZz5mbW0tY29tcHV0ZXItbmFtZTwvc3RyaW5nPgoJCQkJPHN0cmluZz5mbW0tbW9iaWxlbWUtdG9rZW4tRk1NPC9zdHJpbmc+CgkJCQk8c3RyaW5nPmZtbS1tb2JpbGVtZS10b2tlbi1GTU0tQnJpZGdlSGFzQWNjb3VudDwvc3RyaW5nPgoJCQkJPHN0cmluZz5udmRhX2Rydjwvc3RyaW5nPgoJCQkJPHN0cmluZz5wcmV2LWxhbmc6a2JkPC9zdHJpbmc+CgkJCQk8c3RyaW5nPmJhY2tsaWdodC1sZXZlbDwvc3RyaW5nPgoJCQkJPHN0cmluZz5Cb290Q2FtcEhEPC9zdHJpbmc+CgkJCTwvYXJyYXk+CgkJCTxrZXk+OEJFNERGNjEtOTNDQS0xMUQyLUFBMEQtMDBFMDk4MDMyQjhDPC9rZXk+CgkJCTxhcnJheT4KCQkJCTxzdHJpbmc+Qm9vdDAwODA8L3N0cmluZz4KCQkJCTxzdHJpbmc+Qm9vdDAwODE8L3N0cmluZz4KCQkJCTxzdHJpbmc+Qm9vdDAwODI8L3N0cmluZz4KCQkJCTxzdHJpbmc+Qm9vdE5leHQ8L3N0cmluZz4KCQkJCTxzdHJpbmc+Qm9vdE9yZGVyPC9zdHJpbmc+CgkJCTwvYXJyYXk+CgkJPC9kaWN0PgoJCTxrZXk+V3JpdGVGbGFzaDwva2V5PgoJCTx0cnVlLz4KCTwvZGljdD4KCTxrZXk+UGxhdGZvcm1JbmZvPC9rZXk+Cgk8ZGljdD4KCQk8a2V5PkF1dG9tYXRpYzwva2V5PgoJCTx0cnVlLz4KCQk8a2V5PkN1c3RvbU1lbW9yeTwva2V5PgoJCTxmYWxzZS8+CgkJPGtleT5HZW5lcmljPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+QWR2aXNlRmVhdHVyZXM8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5NTEI8L2tleT4KCQkJPHN0cmluZz5DMDI5MDQyMDA0TkpHMzZVRTwvc3RyaW5nPgoJCQk8a2V5Pk1heEJJT1NWZXJzaW9uPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UHJvY2Vzc29yVHlwZTwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8a2V5PlJPTTwva2V5PgoJCQk8ZGF0YT4KCQkJVERKMVRRVmsKCQkJPC9kYXRhPgoJCQk8a2V5PlNwb29mVmVuZG9yPC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5TeXN0ZW1NZW1vcnlTdGF0dXM8L2tleT4KCQkJPHN0cmluZz5BdXRvPC9zdHJpbmc+CgkJCTxrZXk+U3lzdGVtUHJvZHVjdE5hbWU8L2tleT4KCQkJPHN0cmluZz5pTWFjUHJvMSwxPC9zdHJpbmc+CgkJCTxrZXk+U3lzdGVtU2VyaWFsTnVtYmVyPC9rZXk+CgkJCTxzdHJpbmc+QzAyWTVDWURIWDg3PC9zdHJpbmc+CgkJCTxrZXk+U3lzdGVtVVVJRDwva2V5PgoJCQk8c3RyaW5nPjExOEU2MUUyLUQ2OTctNDAxOS04MkFDLTM0NkI3MTlGRTlCQTwvc3RyaW5nPgoJCTwvZGljdD4KCQk8a2V5PlVwZGF0ZURhdGFIdWI8L2tleT4KCQk8dHJ1ZS8+CgkJPGtleT5VcGRhdGVOVlJBTTwva2V5PgoJCTx0cnVlLz4KCQk8a2V5PlVwZGF0ZVNNQklPUzwva2V5PgoJCTx0cnVlLz4KCQk8a2V5PlVwZGF0ZVNNQklPU01vZGU8L2tleT4KCQk8c3RyaW5nPkNyZWF0ZTwvc3RyaW5nPgoJCTxrZXk+VXNlUmF3VXVpZEVuY29kaW5nPC9rZXk+CgkJPGZhbHNlLz4KCTwvZGljdD4KCTxrZXk+VUVGSTwva2V5PgoJPGRpY3Q+CgkJPGtleT5BUEZTPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+RW5hYmxlSnVtcHN0YXJ0PC9rZXk+CgkJCTx0cnVlLz4KCQkJPGtleT5HbG9iYWxDb25uZWN0PC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+SGlkZVZlcmJvc2U8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5Pkp1bXBzdGFydEhvdFBsdWc8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5NaW5EYXRlPC9rZXk+CgkJCTxpbnRlZ2VyPi0xPC9pbnRlZ2VyPgoJCQk8a2V5Pk1pblZlcnNpb248L2tleT4KCQkJPGludGVnZXI+LTE8L2ludGVnZXI+CgkJPC9kaWN0PgoJCTxrZXk+QXBwbGVJbnB1dDwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkFwcGxlRXZlbnQ8L2tleT4KCQkJPHN0cmluZz5CdWlsdGluPC9zdHJpbmc+CgkJCTxrZXk+Q3VzdG9tRGVsYXlzPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+R3JhcGhpY3NJbnB1dE1pcnJvcmluZzwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+S2V5SW5pdGlhbERlbGF5PC9rZXk+CgkJCTxpbnRlZ2VyPjUwPC9pbnRlZ2VyPgoJCQk8a2V5PktleVN1YnNlcXVlbnREZWxheTwva2V5PgoJCQk8aW50ZWdlcj41PC9pbnRlZ2VyPgoJCQk8a2V5PlBvaW50ZXJEd2VsbENsaWNrVGltZW91dDwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8a2V5PlBvaW50ZXJEd2VsbERvdWJsZUNsaWNrVGltZW91dDwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8a2V5PlBvaW50ZXJEd2VsbFJhZGl1czwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8a2V5PlBvaW50ZXJQb2xsTWFzazwva2V5PgoJCQk8aW50ZWdlcj4tMTwvaW50ZWdlcj4KCQkJPGtleT5Qb2ludGVyUG9sbE1heDwva2V5PgoJCQk8aW50ZWdlcj44MDwvaW50ZWdlcj4KCQkJPGtleT5Qb2ludGVyUG9sbE1pbjwva2V5PgoJCQk8aW50ZWdlcj4xMDwvaW50ZWdlcj4KCQkJPGtleT5Qb2ludGVyU3BlZWREaXY8L2tleT4KCQkJPGludGVnZXI+MTwvaW50ZWdlcj4KCQkJPGtleT5Qb2ludGVyU3BlZWRNdWw8L2tleT4KCQkJPGludGVnZXI+MTwvaW50ZWdlcj4KCQk8L2RpY3Q+CgkJPGtleT5BdWRpbzwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkF1ZGlvQ29kZWM8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5BdWRpb0RldmljZTwva2V5PgoJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQk8a2V5PkF1ZGlvT3V0TWFzazwva2V5PgoJCQk8aW50ZWdlcj4xPC9pbnRlZ2VyPgoJCQk8a2V5PkF1ZGlvU3VwcG9ydDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkRpc2Nvbm5lY3RIZGE8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5NYXhpbXVtR2Fpbjwva2V5PgoJCQk8aW50ZWdlcj4tMTU8L2ludGVnZXI+CgkJCTxrZXk+TWluaW11bUFzc2lzdEdhaW48L2tleT4KCQkJPGludGVnZXI+LTMwPC9pbnRlZ2VyPgoJCQk8a2V5Pk1pbmltdW1BdWRpYmxlR2Fpbjwva2V5PgoJCQk8aW50ZWdlcj4tNTU8L2ludGVnZXI+CgkJCTxrZXk+UGxheUNoaW1lPC9rZXk+CgkJCTxzdHJpbmc+QXV0bzwvc3RyaW5nPgoJCQk8a2V5PlJlc2V0VHJhZmZpY0NsYXNzPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+U2V0dXBEZWxheTwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCTwvZGljdD4KCQk8a2V5PkNvbm5lY3REcml2ZXJzPC9rZXk+CgkJPHRydWUvPgoJCTxrZXk+RHJpdmVyczwva2V5PgoJCTxhcnJheT4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyZ3VtZW50czwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5Mb2FkRWFybHk8L2tleT4KCQkJCTxmYWxzZS8+CgkJCQk8a2V5PlBhdGg8L2tleT4KCQkJCTxzdHJpbmc+T3BlblJ1bnRpbWUuZWZpPC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyZ3VtZW50czwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz5IRlMrIERyaXZlcjwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkxvYWRFYXJseTwva2V5PgoJCQkJPGZhbHNlLz4KCQkJCTxrZXk+UGF0aDwva2V5PgoJCQkJPHN0cmluZz5PcGVuSGZzUGx1cy5lZmk8L3N0cmluZz4KCQkJPC9kaWN0PgoJCQk8ZGljdD4KCQkJCTxrZXk+QXJndW1lbnRzPC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5Db21tZW50PC9rZXk+CgkJCQk8c3RyaW5nPjwvc3RyaW5nPgoJCQkJPGtleT5FbmFibGVkPC9rZXk+CgkJCQk8dHJ1ZS8+CgkJCQk8a2V5PkxvYWRFYXJseTwva2V5PgoJCQkJPGZhbHNlLz4KCQkJCTxrZXk+UGF0aDwva2V5PgoJCQkJPHN0cmluZz5PcGVuQ2Fub3B5LmVmaTwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJCTxkaWN0PgoJCQkJPGtleT5Bcmd1bWVudHM8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkNvbW1lbnQ8L2tleT4KCQkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCQk8a2V5PkVuYWJsZWQ8L2tleT4KCQkJCTx0cnVlLz4KCQkJCTxrZXk+TG9hZEVhcmx5PC9rZXk+CgkJCQk8ZmFsc2UvPgoJCQkJPGtleT5QYXRoPC9rZXk+CgkJCQk8c3RyaW5nPk9wZW5QYXJ0aXRpb25EeGUuZWZpPC9zdHJpbmc+CgkJCTwvZGljdD4KCQkJPGRpY3Q+CgkJCQk8a2V5PkFyZ3VtZW50czwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+Q29tbWVudDwva2V5PgoJCQkJPHN0cmluZz48L3N0cmluZz4KCQkJCTxrZXk+RW5hYmxlZDwva2V5PgoJCQkJPHRydWUvPgoJCQkJPGtleT5Mb2FkRWFybHk8L2tleT4KCQkJCTxmYWxzZS8+CgkJCQk8a2V5PlBhdGg8L2tleT4KCQkJCTxzdHJpbmc+UmVzZXROdnJhbUVudHJ5LmVmaTwvc3RyaW5nPgoJCQk8L2RpY3Q+CgkJPC9hcnJheT4KCQk8a2V5PklucHV0PC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+S2V5RmlsdGVyaW5nPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+S2V5Rm9yZ2V0VGhyZXNob2xkPC9rZXk+CgkJCTxpbnRlZ2VyPjU8L2ludGVnZXI+CgkJCTxrZXk+S2V5U3VwcG9ydDwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+S2V5U3VwcG9ydE1vZGU8L2tleT4KCQkJPHN0cmluZz5BdXRvPC9zdHJpbmc+CgkJCTxrZXk+S2V5U3dhcDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlBvaW50ZXJTdXBwb3J0PC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UG9pbnRlclN1cHBvcnRNb2RlPC9rZXk+CgkJCTxzdHJpbmc+QVNVUzwvc3RyaW5nPgoJCQk8a2V5PlRpbWVyUmVzb2x1dGlvbjwva2V5PgoJCQk8aW50ZWdlcj41MDAwMDwvaW50ZWdlcj4KCQk8L2RpY3Q+CgkJPGtleT5PdXRwdXQ8L2tleT4KCQk8ZGljdD4KCQkJPGtleT5DbGVhclNjcmVlbk9uTW9kZVN3aXRjaDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkNvbnNvbGVGb250PC9rZXk+CgkJCTxzdHJpbmc+PC9zdHJpbmc+CgkJCTxrZXk+Q29uc29sZU1vZGU8L2tleT4KCQkJPHN0cmluZz48L3N0cmluZz4KCQkJPGtleT5EaXJlY3RHb3BSZW5kZXJpbmc8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Gb3JjZVJlc29sdXRpb248L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Hb3BCdXJzdE1vZGU8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Hb3BQYXNzVGhyb3VnaDwva2V5PgoJCQk8c3RyaW5nPkRpc2FibGVkPC9zdHJpbmc+CgkJCTxrZXk+SWdub3JlVGV4dEluR3JhcGhpY3M8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5Jbml0aWFsTW9kZTwva2V5PgoJCQk8c3RyaW5nPkF1dG88L3N0cmluZz4KCQkJPGtleT5Qcm92aWRlQ29uc29sZUdvcDwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+UmVjb25uZWN0R3JhcGhpY3NPbkNvbm5lY3Q8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5SZWNvbm5lY3RPblJlc0NoYW5nZTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlJlcGxhY2VUYWJXaXRoU3BhY2U8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5SZXNvbHV0aW9uPC9rZXk+CgkJCTxzdHJpbmc+MTkyMHgxMDgwQDMyPC9zdHJpbmc+CgkJCTxrZXk+U2FuaXRpc2VDbGVhclNjcmVlbjwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlRleHRSZW5kZXJlcjwva2V5PgoJCQk8c3RyaW5nPkJ1aWx0aW5HcmFwaGljczwvc3RyaW5nPgoJCQk8a2V5PlVJU2NhbGU8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5VZ2FQYXNzVGhyb3VnaDwva2V5PgoJCQk8ZmFsc2UvPgoJCTwvZGljdD4KCQk8a2V5PlByb3RvY29sT3ZlcnJpZGVzPC9rZXk+CgkJPGRpY3Q+CgkJCTxrZXk+QXBwbGVBdWRpbzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlQm9vdFBvbGljeTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlRGVidWdMb2c8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5BcHBsZUVnMkluZm88L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5BcHBsZUZyYW1lYnVmZmVySW5mbzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlSW1hZ2VDb252ZXJzaW9uPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+QXBwbGVJbWc0VmVyaWZpY2F0aW9uPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+QXBwbGVLZXlNYXA8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5BcHBsZVJ0Y1JhbTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlU2VjdXJlQm9vdDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkFwcGxlU21jSW88L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5BcHBsZVVzZXJJbnRlcmZhY2VUaGVtZTwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkRhdGFIdWI8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5EZXZpY2VQcm9wZXJ0aWVzPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+RmlybXdhcmVWb2x1bWU8L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5Pkhhc2hTZXJ2aWNlczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5Pk9TSW5mbzwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlBjaUlvPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+VW5pY29kZUNvbGxhdGlvbjwva2V5PgoJCQk8ZmFsc2UvPgoJCTwvZGljdD4KCQk8a2V5PlF1aXJrczwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkFjdGl2YXRlSHBldFN1cHBvcnQ8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5EaXNhYmxlU2VjdXJpdHlQb2xpY3k8L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5FbmFibGVWZWN0b3JBY2NlbGVyYXRpb248L2tleT4KCQkJPHRydWUvPgoJCQk8a2V5PkVuYWJsZVZteDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PkV4aXRCb290U2VydmljZXNEZWxheTwva2V5PgoJCQk8aW50ZWdlcj4wPC9pbnRlZ2VyPgoJCQk8a2V5PkZvcmNlT2NXcml0ZUZsYXNoPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+Rm9yZ2VVZWZpU3VwcG9ydDwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5Pklnbm9yZUludmFsaWRGbGV4UmF0aW88L2tleT4KCQkJPGZhbHNlLz4KCQkJPGtleT5SZWxlYXNlVXNiT3duZXJzaGlwPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+UmVsb2FkT3B0aW9uUm9tczwva2V5PgoJCQk8ZmFsc2UvPgoJCQk8a2V5PlJlcXVlc3RCb290VmFyUm91dGluZzwva2V5PgoJCQk8dHJ1ZS8+CgkJCTxrZXk+UmVzaXplR3B1QmFyczwva2V5PgoJCQk8aW50ZWdlcj4tMTwvaW50ZWdlcj4KCQkJPGtleT5SZXNpemVVc2VQY2lSYklvPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+U2hpbVJldGFpblByb3RvY29sPC9rZXk+CgkJCTxmYWxzZS8+CgkJCTxrZXk+VHNjU3luY1RpbWVvdXQ8L2tleT4KCQkJPGludGVnZXI+MDwvaW50ZWdlcj4KCQkJPGtleT5VbmJsb2NrRnNDb25uZWN0PC9rZXk+CgkJCTxmYWxzZS8+CgkJPC9kaWN0PgoJCTxrZXk+UmVzZXJ2ZWRNZW1vcnk8L2tleT4KCQk8YXJyYXk+PC9hcnJheT4KCQk8a2V5PlVubG9hZDwva2V5PgoJCTxhcnJheT48L2FycmF5PgoJPC9kaWN0Pgo8L2RpY3Q+CjwvcGxpc3Q+Cg=="
OC_SSDT_DTGP_B64="U1NEVGQAAAACv0tHUCAAAERUUEcAAAAAABAAAElOVEwJBRkgFD9EVEdQBaAwk2gREwoQxre1oBgTHESwyf5pXq+Um6AYk2kBoAyTagBwEQMBA2ykAaAGk2oBpAFwEQMBAGykAA=="
OC_SSDT_EC_B64="U1NEVAsBAAACWEFDRFQAAFNzZHRFQwAAABAAAElOVEwJBRkgoA8AFVwuX1NCX1BDSTAGABBGDVxfU0JfW4JPCFVTQlgIX0FEUgAUQghfRFNNBKAJk2oApBEDAQOkEk8GCA1rVVNCU2xlZXBQb3dlclN1cHBseQAL7BMNa1VTQlNsZWVwUG9ydEN1cnJlbnRMaW1pdAALNAgNa1VTQldha2VQb3dlclN1cHBseQAL7BMNa1VTQldha2VQb3J0Q3VycmVudExpbWl0AAs0CBA9XC5fU0JfUENJMFuCMEVDX18IX0hJRA1BQ0lEMDAwMQAUG19TVEEAoBBfT1NJDURhcndpbgCkCg+hA6QA"
OC_SSDT_EHCI_B64="U1NEVKMAAAABv0tHUAAAAFFFTVVVU0IAAAAAAElOVEwJBRkgoCEAFVwuX1NCX1BDSTAGABVcLwNfU0JfUENJMFMzOF8GABBMBVwuX1NCX1BDSTBbgg9FSDAxCF9BRFIMBwAHABALUzM4XwhfU1RBAFuCD1VIQzEIX0FEUgwAAAcAW4IPVUhDMghfQURSDAEABwBbgg9VSEMzCF9BRFIMAgAHAA=="
OC_SSDT_PLUG_B64="U1NEVLEAAAACw0NwdVJlZkNwdVBsdWcAADAAAElOVEwJBRkgoBQAFVwvA19TQl9DUFVTQzAwMAwAEEcHXC8DX1NCX0NQVVNDMDAwFD9EVEdQBaAwk2gREwoQxre1oBgTHESwyf5pXq+Um6AYk2kBoAyTagBwEQMBA2ykAaAGk2oBpAFwEQMBAGykABQlX0RTTQRwEhACDXBsdWdpbi10eXBlAAFgRFRHUGhpamtxYKRg"
OC_MCE_INFO_PLIST_B64="PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPCFET0NUWVBFIHBsaXN0IFBVQkxJQyAiLS8vQXBwbGUvL0RURCBQTElTVCAxLjAvL0VOIiAiaHR0cDovL3d3dy5hcHBsZS5jb20vRFREcy9Qcm9wZXJ0eUxpc3QtMS4wLmR0ZCI+CjxwbGlzdCB2ZXJzaW9uPSIxLjAiPgo8ZGljdD4KCTxrZXk+Q0ZCdW5kbGVEZXZlbG9wbWVudFJlZ2lvbjwva2V5PgoJPHN0cmluZz5FbmdsaXNoPC9zdHJpbmc+Cgk8a2V5PkNGQnVuZGxlR2V0SW5mb1N0cmluZzwva2V5PgoJPHN0cmluZz5NQ0VSZXBvcnRlckRpc2FibGVyIDAuNSwgQ29weXJpZ2h0IChHUEx2MikgwqkgMjAxNyBieSBSZWhhYk1hbi4gQWxsIHJpZ2h0cyByZXNlcnZlZC48L3N0cmluZz4KCTxrZXk+Q0ZCdW5kbGVJZGVudGlmaWVyPC9rZXk+Cgk8c3RyaW5nPm9yZy5yZWhhYm1hbi5kaXNhYmxlci5NQ0VSZXBvcnRlcjwvc3RyaW5nPgoJPGtleT5DRkJ1bmRsZUluZm9EaWN0aW9uYXJ5VmVyc2lvbjwva2V5PgoJPHN0cmluZz42LjA8L3N0cmluZz4KCTxrZXk+Q0ZCdW5kbGVOYW1lPC9rZXk+Cgk8c3RyaW5nPk1DRVJlcG9ydGVyRGlzYWJsZXI8L3N0cmluZz4KCTxrZXk+Q0ZCdW5kbGVQYWNrYWdlVHlwZTwva2V5PgoJPHN0cmluZz5LRVhUPC9zdHJpbmc+Cgk8a2V5PkNGQnVuZGxlVmVyc2lvbjwva2V5PgoJPHN0cmluZz4wLjU8L3N0cmluZz4KCTxrZXk+SU9LaXRQZXJzb25hbGl0aWVzPC9rZXk+Cgk8ZGljdD4KCQk8a2V5Pk1DRUludGVycnVwdENvbnRyb2xsZXJEaXNhYmxlcjwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkNGQnVuZGxlSWRlbnRpZmllcjwva2V5PgoJCQk8c3RyaW5nPmNvbS5hcHBsZS5kcml2ZXIuQXBwbGVJbnRlbE1DRVJlcG9ydGVyPC9zdHJpbmc+CgkJCTxrZXk+SU9DbGFzczwva2V5PgoJCQk8c3RyaW5nPklPU2VydmljZTwvc3RyaW5nPgoJCQk8a2V5PklPTWF0Y2hDYXRlZ29yeTwva2V5PgoJCQk8c3RyaW5nPkFwcGxlSW50ZWxNQ0VJbnRlcnJ1cHRDb250cm9sbGVyPC9zdHJpbmc+CgkJCTxrZXk+SU9Qcm9iZVNjb3JlPC9rZXk+CgkJCTxpbnRlZ2VyPjUwMDA8L2ludGVnZXI+CgkJCTxrZXk+SU9Qcm9wZXJ0eU1hdGNoPC9rZXk+CgkJCTxhcnJheT4KCQkJCTxkaWN0PgoJCQkJCTxrZXk+Ym9hcmQtaWQ8L2tleT4KCQkJCQk8c3RyaW5nPk1hYy1GNjBERUI4MUZGMzBBQ0Y2PC9zdHJpbmc+CgkJCQk8L2RpY3Q+CgkJCQk8ZGljdD4KCQkJCQk8a2V5PmJvYXJkLWlkPC9rZXk+CgkJCQkJPHN0cmluZz5NYWMtN0JBNUIyRDlFNDJEREQ5NDwvc3RyaW5nPgoJCQkJPC9kaWN0PgoJCQkJPGRpY3Q+CgkJCQkJPGtleT5ib2FyZC1pZDwva2V5PgoJCQkJCTxzdHJpbmc+TWFjLTI3QUQyRjkxOEFFNjhGNjE8L3N0cmluZz4KCQkJCTwvZGljdD4KCQkJPC9hcnJheT4KCQkJPGtleT5JT1Byb3ZpZGVyQ2xhc3M8L2tleT4KCQkJPHN0cmluZz5JT1BsYXRmb3JtRXhwZXJ0RGV2aWNlPC9zdHJpbmc+CgkJPC9kaWN0PgoJCTxrZXk+TUNFUmVwb3J0ZXJEaXNhYmxlcjwva2V5PgoJCTxkaWN0PgoJCQk8a2V5PkNGQnVuZGxlSWRlbnRpZmllcjwva2V5PgoJCQk8c3RyaW5nPmNvbS5hcHBsZS5kcml2ZXIuQXBwbGVJbnRlbE1DRVJlcG9ydGVyPC9zdHJpbmc+CgkJCTxrZXk+SU9DbGFzczwva2V5PgoJCQk8c3RyaW5nPklPU2VydmljZTwvc3RyaW5nPgoJCQk8a2V5PklPTWF0Y2hDYXRlZ29yeTwva2V5PgoJCQk8c3RyaW5nPkFwcGxlSW50ZWxNQ0VSZXBvcnRlcjwvc3RyaW5nPgoJCQk8a2V5PklPUHJvYmVTY29yZTwva2V5PgoJCQk8aW50ZWdlcj41MDAwPC9pbnRlZ2VyPgoJCQk8a2V5PklPUHJvdmlkZXJDbGFzczwva2V5PgoJCQk8c3RyaW5nPkFwcGxlSW50ZWxNQ0VJbnRlcnJ1cHROdWI8L3N0cmluZz4KCQk8L2RpY3Q+Cgk8L2RpY3Q+CjwvZGljdD4KPC9wbGlzdD4K"

# ── Assemble OpenCore boot image from pinned upstream releases ──
# Mirrors src/osx_proxmox_next/oc_builder.py; tests diff both sides.

# Download a component archive into CACHE_DIR (reusing a cached copy when its
# hash still matches) and verify the pinned SHA-256. Returns 1 on any failure
# so the caller can fall back to the prebuilt ISO.
function fetch_oc_component() {
  local name="$1" dest="$2"
  local url="${OC_COMPONENT_URLS[$name]}" sha="${OC_COMPONENT_SHA256[$name]}"
  if [ -f "$dest" ] && echo "$sha  $dest" | sha256sum -c --status 2>/dev/null; then
    return 0
  fi
  rm -f "$dest"
  curl -fsSL --retry 3 --max-time 600 -o "$dest" "$url" || { rm -f "$dest"; return 1; }
  # A mismatch is the tamper signal: hard-stop instead of silently falling
  # back to the unpinned prebuilt ISO. Keep in sync with oc_builder.py
  # ChecksumError. A plain download failure above returns 1 (fallback OK).
  echo "$sha  $dest" | sha256sum -c --status || {
    msg_error "Checksum mismatch for ${name} (${url})"
    echo -e "  Expected: ${sha}"
    echo -e "  The upstream file changed or the download was tampered with. Aborting."
    rm -f "$dest"
    exit 1
  }
}

# Lay out the EFI tree in $1 from the pinned components plus the embedded
# config.plist, SSDTs, and MCEReporterDisabler. Returns 1 on any failure.
function assemble_opencore_tree() {
  local tree="$1"
  local name dest
  declare -A archives=()
  for name in "${!OC_COMPONENT_URLS[@]}"; do
    dest="$CACHE_DIR/${OC_COMPONENT_URLS[$name]##*/}"
    fetch_oc_component "$name" "$dest" || return 1
    archives[$name]="$dest"
  done

  OC_TREE="$tree" \
  OC_ZIP_OPENCORE="${archives[OpenCorePkg]}" \
  OC_ZIP_LILU="${archives[Lilu]}" \
  OC_ZIP_VSMC="${archives[VirtualSMC]}" \
  OC_ZIP_WEG="${archives[WhateverGreen]}" \
  OC_ZIP_ALC="${archives[AppleALC]}" \
  OC_ZIP_CRYPTEX="${archives[CryptexFixup]}" \
  OC_ZIP_RE="${archives[RestrictEvents]}" \
  OC_TAR_OCBD="${archives[OcBinaryData]}" \
  python3 -c '
import os, shutil, sys, tarfile, zipfile

tree = os.environ["OC_TREE"]

def check(name):
    if name.startswith("/") or ".." in name.split("/"):
        raise SystemExit("unsafe archive member: " + name)

def extract_files(zip_path, mapping):
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for member, rel in mapping.items():
            if member not in names:
                raise SystemExit("missing " + member + " in " + zip_path)
            out = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)

def extract_subtree(zip_path, prefix, strip=0):
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            check(info.filename)
            rel = "/".join(info.filename.split("/")[strip:])
            out = os.path.join(tree, "EFI/OC/Kexts", rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if count == 0:
        raise SystemExit("no members under " + prefix + " in " + zip_path)

# Keep this file map in sync with oc_builder._OC_PKG_FILES.
extract_files(os.environ["OC_ZIP_OPENCORE"], {
    "X64/EFI/BOOT/BOOTx64.efi": "EFI/BOOT/BOOTx64.efi",
    "X64/EFI/OC/OpenCore.efi": "EFI/OC/OpenCore.efi",
    "X64/EFI/OC/Drivers/OpenRuntime.efi": "EFI/OC/Drivers/OpenRuntime.efi",
    "X64/EFI/OC/Drivers/OpenHfsPlus.efi": "EFI/OC/Drivers/OpenHfsPlus.efi",
    "X64/EFI/OC/Drivers/OpenCanopy.efi": "EFI/OC/Drivers/OpenCanopy.efi",
    "X64/EFI/OC/Drivers/OpenPartitionDxe.efi": "EFI/OC/Drivers/OpenPartitionDxe.efi",
    "X64/EFI/OC/Drivers/ResetNvramEntry.efi": "EFI/OC/Drivers/ResetNvramEntry.efi",
    "X64/EFI/OC/Tools/OpenShell.efi": "EFI/OC/Tools/Shell.efi",
    "X64/EFI/OC/Tools/ResetSystem.efi": "EFI/OC/Tools/ResetSystem.efi",
})
for env, prefix, strip in (
    ("OC_ZIP_LILU", "Lilu.kext/", 0),
    ("OC_ZIP_WEG", "WhateverGreen.kext/", 0),
    ("OC_ZIP_ALC", "AppleALC.kext/", 0),
    ("OC_ZIP_CRYPTEX", "CryptexFixup.kext/", 0),
    ("OC_ZIP_RE", "RestrictEvents.kext/", 0),
    ("OC_ZIP_VSMC", "Kexts/VirtualSMC.kext/", 1),
):
    extract_subtree(os.environ[env], prefix, strip)

# OpenCanopy resources: Font, Label, and the Syrah icon set only (AudioDxe
# stays disabled, so Audio is skipped). Same subtrees as oc_builder.
subtrees = ("Resources/Font/", "Resources/Label/", "Resources/Image/Acidanthera/Syrah/")
count = 0
with tarfile.open(os.environ["OC_TAR_OCBD"], "r:gz") as tar:
    for member in tar:
        if not member.isfile():
            continue
        check(member.name)
        rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
        if not any(rel.startswith(s) for s in subtrees):
            continue
        out = os.path.join(tree, "EFI/OC", rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        src = tar.extractfile(member)
        with open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        count += 1
if count == 0:
    raise SystemExit("no OpenCanopy resources found in OcBinaryData archive")
' || return 1

  mkdir -p "$tree/EFI/OC/ACPI" "$tree/EFI/OC/Kexts/MCEReporterDisabler.kext/Contents" || return 1
  echo "$OC_CONFIG_TEMPLATE_B64" | base64 -d > "$tree/EFI/OC/config.plist" || return 1
  echo "$OC_SSDT_DTGP_B64" | base64 -d > "$tree/EFI/OC/ACPI/SSDT-DTGP.aml" || return 1
  echo "$OC_SSDT_EC_B64" | base64 -d > "$tree/EFI/OC/ACPI/SSDT-EC.aml" || return 1
  echo "$OC_SSDT_EHCI_B64" | base64 -d > "$tree/EFI/OC/ACPI/SSDT-EHCI.aml" || return 1
  echo "$OC_SSDT_PLUG_B64" | base64 -d > "$tree/EFI/OC/ACPI/SSDT-PLUG.aml" || return 1
  echo "$OC_MCE_INFO_PLIST_B64" | base64 -d > "$tree/EFI/OC/Kexts/MCEReporterDisabler.kext/Contents/Info.plist" || return 1
}

# Undo a partial image build: unmount, detach, remove temporaries.
function _oc_assemble_abort() {
  local mnt="$1" iloop="$2" img="$3" tree="$4"
  [ -n "$mnt" ] && { umount "$mnt" 2>/dev/null || umount -l "$mnt" 2>/dev/null || true; rm -rf "$mnt"; }
  [ -n "$iloop" ] && losetup -d "$iloop" 2>/dev/null
  BUILD_DEST_MNT="" BUILD_LOOP=""
  rm -f "$img"
  rm -rf "$tree"
  echo -e "  ${YW}WARN: OpenCore image assembly failed at the disk stage${CL}"
}

# Assemble the OpenCore ISO at $1. Returns non-zero on ANY failure so the
# caller can fall back to the prebuilt ISO; it must never exit the script
# itself (checksum mismatches excepted: fetch_oc_component hard-stops).
# Deliberately avoids setup_loop/safe_mount, which exit on failure.
function assemble_opencore_iso() {
  local dest_iso="$1"
  local tree="$TEMP_DIR/oc-tree"
  local img="${dest_iso}.part"
  local iloop="" mnt=""

  rm -rf "$tree"
  mkdir -p "$tree" || return 1
  assemble_opencore_tree "$tree" || { rm -rf "$tree"; return 1; }

  cleanup_stale_loops "$img"
  rm -f "$img"
  truncate -s 128M "$img" || { _oc_assemble_abort "" "" "$img" "$tree"; return 1; }
  sgdisk -Z "$img" &>/dev/null
  sgdisk -n 1:0:0 -t 1:EF00 -c 1:OPENCORE "$img" &>/dev/null \
    || { _oc_assemble_abort "" "" "$img" "$tree"; return 1; }

  iloop=$(losetup -fP --show "$img" 2>/dev/null) || iloop=""
  if [ -z "$iloop" ] || [ ! -b "$iloop" ]; then
    _oc_assemble_abort "" "" "$img" "$tree"
    return 1
  fi
  BUILD_LOOP="$iloop"

  local _i
  for _i in 1 2 3 4 5; do
    partprobe "$iloop" 2>/dev/null || true
    [ -b "${iloop}p1" ] && break
    sleep 1
  done
  if [ ! -b "${iloop}p1" ] || ! mkfs.fat -F 32 -n OPENCORE "${iloop}p1" &>/dev/null; then
    _oc_assemble_abort "" "$iloop" "$img" "$tree"
    return 1
  fi

  mnt=$(mktemp -d) || { _oc_assemble_abort "" "$iloop" "$img" "$tree"; return 1; }
  if ! mount "${iloop}p1" "$mnt" 2>/dev/null || ! mountpoint -q "$mnt"; then
    _oc_assemble_abort "$mnt" "$iloop" "$img" "$tree"
    return 1
  fi
  BUILD_DEST_MNT="$mnt"
  if ! cp -a "$tree"/. "$mnt"/; then
    _oc_assemble_abort "$mnt" "$iloop" "$img" "$tree"
    return 1
  fi

  { umount "$mnt" 2>/dev/null || umount -l "$mnt"; } || {
    _oc_assemble_abort "$mnt" "$iloop" "$img" "$tree"
    return 1
  }
  losetup -d "$iloop" 2>/dev/null
  rm -rf "$mnt" "$tree"
  BUILD_DEST_MNT="" BUILD_LOOP=""
  mv "$img" "$dest_iso" || { rm -f "$img"; return 1; }
}

# ── Unattended install driver (BETA) ──
# Mirrors src/osx_proxmox_next/unattended.py; tests diff the constants and
# key sequences. Frame-size heuristics: >5000000 bytes = 1920x1080 OpenCore
# picker, smaller = 1280x800 macOS.
UNATTENDED_PICKER_MIN_BYTES=5000000
UNATTENDED_RECOVERY_SETTLE=240
UNATTENDED_RECOVERY_STILL_MAX=600
UNATTENDED_RECOVERY_STILL_FRAMES=3
UNATTENDED_PICKER_TIMEOUT=600
UNATTENDED_INSTALL_REBOOT_TIMEOUT=1800
UNATTENDED_RECOVERY_TIMEOUT=900
UNATTENDED_DONE_QUIET=900
UNATTENDED_DONE_MOTION=120
UNATTENDED_HUNG_STILL=600
UNATTENDED_PICKER_REPRESS=30
UNATTENDED_MAX_POWER_CYCLES=2
UNATTENDED_MAX_RECOVERY_RESETS=2
# Picker auto-boot once recovery is detached (script_renderer.py PICKER_TIMEOUT_INSTALLED).
PICKER_TIMEOUT_INSTALLED=15
UNATTENDED_TOTAL_BUDGET=10800
UNATTENDED_POLL=6

function unattended_frame_size() {
  local vmid="$1" probe="/tmp/osx-next-unattended-$1.ppm"
  rm -f "$probe"
  echo "screendump $probe" | qm monitor "$vmid" >/dev/null 2>&1
  sleep 0.6
  stat -c %s "$probe" 2>/dev/null || echo 0
}

# md5 of the frame unattended_frame_size last wrote, without dumping again.
function unattended_probe_hash() {
  md5sum "/tmp/osx-next-unattended-$1.ppm" 2>/dev/null | cut -d' ' -f1
}

function unattended_frame_hash() {
  local vmid="$1"
  [ "$(unattended_frame_size "$vmid")" == "0" ] && return 0
  unattended_probe_hash "$vmid"
}

# Watch the screen through the recovery settle; fail (1) if the 1080p picker
# comes back. OVMF draws at 1280x800 like recovery, so a guest that reset
# instead of booting recovery passes the size check, and the Terminal
# navigation would be typed into the picker that follows. A real recovery
# boot never returns to a 1080p frame.
function unattended_settle_recovery() {
  local vmid="$1" t0
  t0=$(date +%s)
  while (( $(date +%s) - t0 < UNATTENDED_RECOVERY_SETTLE )); do
    (( $(unattended_frame_size "$vmid") > UNATTENDED_PICKER_MIN_BYTES )) && return 1
    sleep "$UNATTENDED_POLL"
  done
  return 0
}

# Hold until the recovery screen stops animating. A cold first boot can still
# be in Recovery Assistant ("Examining volumes", a spinner) when the settle
# expires; its menu bar carries no Utilities menu, so the Terminal navigation
# walks the wrong menu bar, the command lands in the utilities window that
# appears later and its closing return runs "Restore from Time Machine".
function unattended_wait_still() {
  local vmid="$1" t0 seen cur still=1
  t0=$(date +%s)
  seen=$(unattended_frame_hash "$vmid")
  while (( $(date +%s) - t0 < UNATTENDED_RECOVERY_STILL_MAX )); do
    sleep "$UNATTENDED_POLL"
    cur=$(unattended_frame_hash "$vmid")
    if [ -n "$cur" ] && [ "$cur" == "$seen" ]; then
      still=$((still + 1))
      (( still >= UNATTENDED_RECOVERY_STILL_FRAMES )) && return 0
    else
      seen="$cur"; still=1
    fi
  done
  msg_info "Unattended: recovery screen never stopped moving; navigating anyway"
}

function unattended_type() {
  local vmid="$1" text="$2" i ch key
  for ((i = 0; i < ${#text}; i++)); do
    ch="${text:$i:1}"
    case "$ch" in
      " ") key="spc" ;;      ".") key="dot" ;;       "/") key="slash" ;;
      "-") key="minus" ;;    "_") key="shift-minus" ;; ",") key="comma" ;;
      ";") key="semicolon" ;; ":") key="shift-semicolon" ;; "=") key="equal" ;;
      "+") key="shift-equal" ;; \') key="apostrophe" ;; \") key="shift-apostrophe" ;;
      "\\") key="backslash" ;; "|") key="shift-backslash" ;; "*") key="shift-8" ;;
      '$') key="shift-4" ;;  "(") key="shift-9" ;;   ")") key="shift-0" ;;
      "&") key="shift-7" ;;  "!") key="shift-1" ;;   "{") key="shift-bracket_left" ;;
      "}") key="shift-bracket_right" ;; "<") key="shift-comma" ;; ">") key="shift-dot" ;;
      "?") key="shift-slash" ;; "#") key="shift-3" ;; "%") key="shift-5" ;;
      "@") key="shift-2" ;;  "^") key="shift-6" ;;   "~") key="shift-grave_accent" ;;
      [A-Z]) key="shift-$(tr "[:upper:]" "[:lower:]" <<< "$ch")" ;;
      *) key="$ch" ;;
    esac
    qm sendkey "$vmid" "$key" >/dev/null 2>&1
    sleep 0.12
  done
}

# The one shell line typed into recovery Terminal: erase the disk matched by
# its exact diskutil size, then run the non-interactive installer.
function unattended_install_command() {
  local disk_gb="$1" size
  size=$(awk -v g="$disk_gb" 'BEGIN{printf "%.1f", g*1.073741824}' | sed 's/\./\\./')
  printf '%s' "d=\$(diskutil list | awk '/\\*$size GB/{print \$NF; exit}') && diskutil eraseDisk APFS MACOS \$d && \"/Install macOS \"*.app/Contents/Resources/startosinstall --agreetolicense --volume /Volumes/MACOS --nointeraction"
}

function unattended_wait_stopped() {
  local vmid="$1" _i
  for _i in $(seq 1 40); do
    qm status "$vmid" 2>/dev/null | grep -q stopped && return 0
    sleep 3
  done
  return 1
}

# args: vmid timeout mode(above|below) [repress_key]
# With a repress key, that key is pressed again every UNATTENDED_PICKER_REPRESS
# seconds while the 1080p picker is still on screen: OpenCanopy reaches its
# first 1080p frame before its input is ready, so on a slow boot disk the first
# return is dropped and Timeout=0 parks the picker forever (issue #142).
function unattended_wait_frame() {
  local vmid="$1" timeout="$2" mode="$3" repress="${4:-}" t0 size last_press
  t0=$(date +%s)
  last_press=$((t0 - UNATTENDED_PICKER_REPRESS))
  while (( $(date +%s) - t0 < timeout )); do
    size=$(unattended_frame_size "$vmid")
    if [ "$mode" == "above" ] && (( size > UNATTENDED_PICKER_MIN_BYTES )); then return 0; fi
    if [ "$mode" == "below" ] && (( size > 0 && size < UNATTENDED_PICKER_MIN_BYTES )); then return 0; fi
    if [ -n "$repress" ] && (( size > UNATTENDED_PICKER_MIN_BYTES )) \
       && (( $(date +%s) - last_press >= UNATTENDED_PICKER_REPRESS )); then
      qm sendkey "$vmid" "$repress" >/dev/null 2>&1
      last_press=$(date +%s)
    fi
    sleep "$UNATTENDED_POLL"
  done
  return 1
}

# Save the console of a stalled run: it is gone by the time anyone reads the
# log. PNG needs QEMU 7.1+; older hosts get the raw PPM next to it.
function unattended_save_frame() {
  local vmid="$1" png="/var/log/osx-next-unattended-${1}.png"
  local ppm="/var/log/osx-next-unattended-${1}.ppm"
  echo "screendump $png -f png" | qm monitor "$vmid" >/dev/null 2>&1
  sleep 0.6
  [ -s "$png" ] && { echo "$png"; return 0; }
  echo "screendump $ppm" | qm monitor "$vmid" >/dev/null 2>&1
  sleep 0.6
  [ -s "$ppm" ] && { echo "$ppm"; return 0; }
  return 1
}

# Hold until startosinstall reboots the guest (the first 1080p frame). The
# guest's own restart can wedge inside an EFI runtime call instead: every vCPU
# spins and recovery Terminal stays frozen on "Restarting...". A screen
# unchanged for UNATTENDED_HUNG_STILL counts as that reboot, because the
# stop/start that follows is also the only thing that clears the wedge.
function unattended_wait_first_reboot() {
  local vmid="$1" t0 size digest seen="" last_change
  t0=$(date +%s)
  last_change=$t0
  while (( $(date +%s) - t0 < UNATTENDED_INSTALL_REBOOT_TIMEOUT )); do
    size=$(unattended_frame_size "$vmid")
    (( size > UNATTENDED_PICKER_MIN_BYTES )) && return 0
    digest=$(unattended_probe_hash "$vmid")
    if [ "$digest" != "$seen" ]; then
      seen="$digest"
      last_change=$(date +%s)
    elif [ -n "$digest" ] && (( $(date +%s) - last_change > UNATTENDED_HUNG_STILL )); then
      msg_info "Unattended: guest froze while restarting (screen unchanged for $((UNATTENDED_HUNG_STILL / 60)) min); power-cycling"
      return 0
    fi
    sleep "$UNATTENDED_POLL"
  done
  return 1
}

# Put the OpenCore picker back to auto-boot on a stopped VM's boot disk.
# The image ships Timeout=0 so an install can never auto-boot recovery; once
# recovery is detached that guard has done its job, and left in place every
# later boot waits for a return. Mirrors planner._restore_picker_timeout_script.
# Non-fatal: returns 1 when the disk could not be patched.
function restore_picker_timeout() {
  local vmid="$1" vol path loop mnt rc=1 _i
  vol=$(qm config "$vmid" 2>/dev/null | sed -n 's/^ide0: \([^,]*\).*/\1/p')
  [ -n "$vol" ] || return 1
  path=$(pvesm path "$vol" 2>/dev/null) || return 1
  loop=$(losetup -fP --show "$path" 2>/dev/null) || return 1
  for _i in 1 2 3 4 5; do
    partprobe "$loop" 2>/dev/null || true
    [ -b "${loop}p1" ] && break
    sleep 1
  done
  mnt=$(mktemp -d)
  if [ -b "${loop}p1" ] && mount "${loop}p1" "$mnt" 2>/dev/null; then
    if OC_PLIST="$mnt/EFI/OC/config.plist" OC_TIMEOUT="$PICKER_TIMEOUT_INSTALLED" python3 -c '
import os, plistlib
p = os.environ["OC_PLIST"]
with open(p, "rb") as f:
    c = plistlib.load(f)
c["Misc"]["Boot"]["Timeout"] = int(os.environ["OC_TIMEOUT"])
with open(p, "wb") as f:
    plistlib.dump(c, f)
'; then
      # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
      sed -i 's|<array/>|<array></array>|g; s|<dict/>|<dict></dict>|g; s|<data/>|<data></data>|g' \
        "$mnt/EFI/OC/config.plist" && rc=0
    fi
    sync
    umount "$mnt" 2>/dev/null || true
  fi
  losetup -d "$loop" 2>/dev/null || true
  rmdir "$mnt" 2>/dev/null || true
  return "$rc"
}

# Bash equivalent of `osx-next-cli post-install`: run once the installer has
# rebooted the first time (VM stopped). Detaches recovery, restores the
# picker's auto-boot and boots OpenCore first.
function post_install() {
  local vmid="$1"
  [[ "$vmid" =~ ^[0-9]+$ ]] || { msg_error "Usage: osx-proxmox-next.sh post-install <vmid>"; exit 1; }
  qm status "$vmid" 2>/dev/null | grep -q 'status: stopped' || {
    msg_error "VM $vmid is not stopped. Stop it first: qm stop $vmid"; exit 1; }
  if qm config "$vmid" | grep -q '^ide2:'; then
    qm set "$vmid" --delete ide2 >/dev/null
    msg_ok "Detached the recovery disk"
  fi
  local rc=0
  if restore_picker_timeout "$vmid"; then
    msg_ok "Boot picker auto-boots after ${PICKER_TIMEOUT_INSTALLED}s again"
  else
    msg_error "Could not patch the OpenCore disk; the picker will keep waiting for return"
    rc=1
  fi
  qm set "$vmid" --boot "order=ide0;virtio0" >/dev/null
  msg_ok "Boot order set to OpenCore first"
  return "$rc"
}

# ── Vendor-neutral GPU helpers begin ──
# Bash twin of src/osx_proxmox_next/gpu.py (detection, vfio/IOMMU/BAR checks,
# hostpci and MMIO-window args). Vendor profiles (NullMoth below) sit on top.
GPU_ISSUES=()
GPU_WARNS=()
GPU_HINTS=()
GPU_MMIO_ARG="-global q35-pcihost.pci-hole64-size=64G"

# domain:bus:slot, lowercase, function dropped. Mirrors nvidia_host.normalize_bdf.
function gpu_normalize_bdf() {
  local raw="${1,,}"
  [[ "$raw" =~ ^(([0-9a-f]{4}):)?([0-9a-f]{2}):([0-9a-f]{2})(\.[0-7])?$ ]] || return 1
  echo "${BASH_REMATCH[2]:-0000}:${BASH_REMATCH[3]}:${BASH_REMATCH[4]}"
}

# Read a sysfs hex file (with or without 0x) as a decimal number; empty on failure.
function gpu_sys_num() {
  local v
  v=$(cat "$1" 2>/dev/null) || return 1
  [[ "$v" =~ ^(0x)?[0-9a-fA-F]+$ ]] || return 1
  echo $((16#${v#0x}))
}

# 0 when the host really uses the card as its console: boot_vga is set AND a host display driver is bound or a
# firmware framebuffer (efifb, simplefb) holds one of its BARs in /proc/iomem. Mirrors gpu.is_host_console.
# $1 = the card's .0 sysfs directory, $2 = iomem file (default: proc/iomem beside the sysfs root).
function gpu_host_console() {
  local dev="$1" sys="${GPU_SYSFS:-/sys}" iomem driver rs re _f lo hi name
  iomem="${2:-$(dirname "$sys")/proc/iomem}"
  [ "$(gpu_sys_num "$dev/boot_vga" 2>/dev/null || true)" == "1" ] || return 1
  driver=""
  [ -e "$dev/driver" ] && driver=$(basename "$(readlink -f "$dev/driver")")
  if [ -n "$driver" ] && [ "$driver" != "vfio-pci" ]; then return 0; fi
  [ -r "$iomem" ] || return 1
  while read -r rs re _f; do
    [[ "$rs" =~ ^0x[0-9a-fA-F]+$ && "$re" =~ ^0x[0-9a-fA-F]+$ ]] || continue
    (( re > rs )) || continue
    while read -r lo hi name; do
      case "${name,,}" in efifb|simplefb|bootfb|vesafb) ;; *) continue ;; esac
      (( 16#$lo >= rs && 16#$hi <= re )) && return 0
    done < <(sed -n 's/^ *\([0-9a-f]\+\)-\([0-9a-f]\+\) *: *\([^ ]\+\).*/\1 \2 \3/p' "$iomem")
  done < "$dev/resource"
  return 1
}

# Exact vfio.conf lines for the card's functions. Mirrors gpu.vfio_conf_hint.
function gpu_vfio_hint() {
  local sys="$1" bdf="$2" fn v d c ids="" usb_c=no name vendor=""
  for fn in "$sys"/bus/pci/devices/"$bdf".*; do
    [ -e "$fn" ] || continue
    v=$(gpu_sys_num "$fn/vendor") || continue
    d=$(gpu_sys_num "$fn/device") || continue
    c=$(gpu_sys_num "$fn/class") || c=0
    case $(( c >> 16 )) in 1|2) continue ;; esac
    [ -z "$vendor" ] || [ "$v" == "$vendor" ] || continue
    [ -n "$vendor" ] || vendor="$v"
    ids+="${ids:+,}$(printf '%04x:%04x' "$v" "$d")"
    case $(( c >> 8 )) in 3075|3200) usb_c=yes ;; esac
  done
  GPU_HINTS=("Put this in /etc/modprobe.d/vfio.conf:" "  options vfio-pci ids=${ids}")
  local softdeps=(nouveau nvidia snd_hda_intel)
  [ "${vendor:-4318}" == "4098" ] && softdeps=(amdgpu radeon snd_hda_intel)
  if [ "$usb_c" == "yes" ]; then
    softdeps+=(xhci_pci)
    [ "${vendor:-4318}" == "4098" ] || softdeps+=(i2c_nvidia_gpu)
  fi
  for name in "${softdeps[@]}"; do GPU_HINTS+=("  softdep ${name} pre: vfio-pci"); done
  GPU_HINTS+=("Then run: update-initramfs -u -k all" "Then reboot the host.")
}

# Run every host-side readiness check for one card. $1 = bdf (normalized),
# $2 = PCI vendor id (decimal), $3 = name of a function that succeeds for a
# supported device id (decimal), $4 = printf format for the unsupported message,
# $5 = BAR whose size is checked (default 1), $6 = optional function that prints the
# unsupported message for a decimal device id (replaces $4).
# Fills GPU_ISSUES / GPU_WARNS / GPU_HINTS. Mirrors gpu.check_gpu; GPU_SYSFS and
# GPU_CMDLINE can be overridden for tests.
function gpu_check() {
  local bdf="$1" want_vendor="$2" support_fn="$3" unsupported_fmt="$4" bar="${5:-1}" reason_fn="${6:-}" sys="${GPU_SYSFS:-/sys}" cmdline="${GPU_CMDLINE:-/proc/cmdline}"
  local dev="$sys/bus/pci/devices/$bdf.0" vendor device fn driver bad="" grp member c stray=""
  local -a funcs=()
  if [ ! -e "$dev" ]; then GPU_ISSUES+=("No PCI device at $bdf.0 on this host."); return; fi
  vendor=$(gpu_sys_num "$dev/vendor") || vendor=0
  if (( vendor != want_vendor )); then GPU_ISSUES+=("$bdf.0 is not a vendor $(printf '%04x' "$want_vendor") device."); return; fi
  device=$(gpu_sys_num "$dev/device") || device=0
  if ! "$support_fn" "$device"; then
    if [ -n "$reason_fn" ]; then
      GPU_ISSUES+=("$("$reason_fn" "$device")")
    else
      # shellcheck disable=SC2059
      GPU_ISSUES+=("$(printf "$unsupported_fmt" "$(printf '%04x' "$device")")")
    fi
    return
  fi
  for fn in "$sys"/bus/pci/devices/"$bdf".*; do funcs+=("$(basename "$fn")"); done
  for fn in "${funcs[@]}"; do
    driver=""
    [ -e "$sys/bus/pci/devices/$fn/driver" ] && driver=$(basename "$(readlink -f "$sys/bus/pci/devices/$fn/driver")")
    [ "$driver" == "vfio-pci" ] || bad+="${bad:+, }$fn (${driver:-no driver})"
  done
  if [ -n "$bad" ]; then
    GPU_ISSUES+=("Every function of the card must be bound to vfio-pci. Not bound: ${bad}. The host GPU driver must not hold the card.")
    gpu_vfio_hint "$sys" "$bdf"
  fi
  if [ ! -e "$dev/iommu_group" ]; then
    GPU_ISSUES+=("IOMMU is not active for this card (no iommu_group). Enable VT-d/AMD-Vi in the BIOS and intel_iommu=on iommu=pt (Intel) or iommu=pt (AMD) on the kernel cmdline.")
  else
    grp=$(readlink -f "$dev/iommu_group")
    for member in "$grp"/devices/*; do
      member=$(basename "$member")
      [[ " ${funcs[*]} " == *" $member "* ]] && continue
      c=$(gpu_sys_num "$sys/bus/pci/devices/$member/class") || c=-1
      case $(( c >> 8 )) in 1540|1536) continue ;; esac
      stray+="${stray:+, }$member"
    done
    [ -z "$stray" ] || GPU_ISSUES+=("IOMMU group $(basename "$grp") also holds non-GPU device(s): ${stray}. Passing the card through would pull them in.")
  fi
  if gpu_host_console "$dev" && ! grep -q 'initcall_blacklist=sysfb_init' "$cmdline" 2>/dev/null; then
    GPU_WARNS+=("This card is the host boot GPU and initcall_blacklist=sysfb_init is not on the kernel cmdline. The host framebuffer may hold its memory and the VM can fail to start.")
  fi
  local start end _flags size
  if read -r start end _flags < <(sed -n "$((bar + 1))p" "$dev/resource" 2>/dev/null) && [ -n "${end:-}" ]; then
    size=$(( end - start + 1 ))
    if (( size > 268435456 )); then
      GPU_WARNS+=("BAR${bar} is $(( size / 1048576 )) MiB. Larger than 256 MiB is untested for macOS guests and QEMU may refuse to start. No automatic resize is done.")
    fi
  fi
}

# Mirrors nullmoth.with_mmio_arg / without_mmio_arg.
function gpu_with_mmio_arg() {
  local a="$1"
  if [[ "$a" =~ -global[[:space:]]+q35-pcihost\.pci-hole64-size=[^[:space:]]+ ]]; then
    echo "${a/"${BASH_REMATCH[0]}"/$GPU_MMIO_ARG}"
  else
    echo "$a $GPU_MMIO_ARG" | sed 's/^ *//'
  fi
}

function gpu_without_mmio_arg() {
  local a="$1"
  if [[ "$a" =~ [[:space:]]*-global[[:space:]]+q35-pcihost\.pci-hole64-size=[^[:space:]]+ ]]; then
    a="${a/"${BASH_REMATCH[0]}"/}"
  fi
  echo "$a" | sed 's/^ *//; s/ *$//'
}

# Other VMs that already hold the card, directly or through a resource mapping. Prints "<vmid> running|stopped".
function gpu_users() {
  local vmid="$1" bdf="$2" id status cfg
  while read -r id _ status _; do
    [[ "$id" =~ ^[0-9]+$ ]] || continue
    [ "$id" == "$vmid" ] && continue
    cfg=$(qm config "$id" 2>/dev/null) || continue
    if [ "$(python3 -c 'import json,sys; print(json.dumps({"bdf": sys.argv[1], "cfg_raw": sys.stdin.read()}))' "$bdf" <<<"$cfg" | gpu_host holds)" == "yes" ]; then
      echo "$id $status"
    fi
  done < <(qm list 2>/dev/null | tail -n +2)
}

# ── Vendor-neutral GPU helpers end ──

# ── NullMoth NVIDIA GPU attach (beta) begin ──
# Bash twin of src/osx_proxmox_next/nullmoth.py and nvidia_host.py. The notice,
# pins, device allowlist and OpenCore patcher below are diffed against the
# Python side by tests/test_nullmoth_parity.py; change both together.
NULLMOTH_URL="https://github.com/nullmoth/nvidia-macos-driver"
NULLMOTH_NOTICE_TITLE="NVIDIA GPU support (Beta)"
read -r -d '' NULLMOTH_NOTICE_BODY <<'NULLMOTH_NOTICE' || true
This feature prepares your VM for the NullMoth NVIDIA driver, a third-party project that osx-proxmox-next does not make, review or support. You download and install that driver yourself, at your own risk. We are not responsible for the driver, its installation, or any damage, data loss or instability that follows.

NullMoth only runs with core macOS security turned off: code-signing checks (AMFI) are bypassed, System Integrity Protection is relaxed and Secure Boot is disabled. This is a real security threat: malware has a much easier time on a VM set up this way. Only enable it on a VM you are prepared to treat as untrusted, and keep personal accounts and sensitive data off it.

NullMoth is licensed PolyForm Noncommercial 1.0.0. Driver and docs: https://github.com/nullmoth/nvidia-macos-driver
NULLMOTH_NOTICE
NULLMOTH_ACK_LABEL='I understand the risks and that osx-proxmox-next is not responsible for the NullMoth driver.'
NULLMOTH_ACK_PHRASE="I accept"
# AMFIPass is not an Acidanthera release; this is the build OpenCore Legacy
# Patcher ships, pinned by commit (oc_builder.AMFIPASS).
NULLMOTH_AMFIPASS_URL="https://github.com/dortania/OpenCore-Legacy-Patcher/raw/76516394fddb7ce4526ccbcc5b806795c30e9a89/payloads/Kexts/Acidanthera/AMFIPass-v1.4.1-RELEASE.zip"
NULLMOTH_AMFIPASS_SHA256="07b266145906db41f4b13a7938fbb173ea28888cc1fa65f84417f8820adc961e"
# Turing and newer device ids (vendor 10de), upstream commit
# 6b4752c51b0719862fb46471d383317ef7ecc6f0. Every id from 1E00 up also counts.
NULLMOTH_SUPPORTED_IDS="
1E02 1E04 1E07 1E30 1E36 1E78 1E81 1E82 1E84 1E87 1E89 1E90 1E91 1E93 1EB0
1EB1 1EB5 1EB6 1EC2 1EC7 1ED0 1ED1 1ED3 1EF5 1F02 1F03 1F06 1F07 1F08 1F0A
1F10 1F11 1F12 1F14 1F15 1F36 1F42 1F47 1F50 1F51 1F54 1F55 1F76 1F82 1F83
1F91 1F95 1F96 1F97 1F98 1F99 1F9C 1F9D 1F9F 1FA0 1FB0 1FB1 1FB2 1FB6 1FB7
1FB8 1FB9 1FBA 1FBB 1FBC 1FDD 1FF0 1FF2 1FF9 2182 2184 2187 2188 2191 2192
21C4 21D1 2203 2204 2206 2207 2208 220A 2216 2230 2231 2232 2233 2414 2420
2438 2460 2482 2484 2486 2487 2488 2489 249C 249D 24A0 24B0 24B1 24B6 24B7
24B8 24B9 24BA 24BB 24C7 24C9 24DC 24DD 24E0 24FA 2503 2504 2507 2508 2520
2521 2523 2531 2544 2560 2563 2571 2582 2584 25A0 25A2 25A5 25A6 25A7 25A9
25AA 25AB 25AC 25AD 25B0 25B2 25B8 25B9 25BA 25BB 25BC 25BD 25E0 25E2 25E5
25EC 25ED 25F9 25FA 25FB 2684 2685 2689 26B1 26B2 26B3 2702 2704 2705 2709
2717 2730 2757 2770 2782 2783 2786 2788 27A0 27B0 27B1 27B2 27BA 27BB 27E0
27FB 2803 2805 2808 2820 2822 2838 2860 2882 28A0 28A1 28A3 28B0 28B8 28B9
28BA 28BB 28E0 28E1 28E3 28F8 2B85 2B87 2B8C 2BB1 2BB3 2BB4 2BB5 2BB9 2C02
2C05 2C09 2C18 2C19 2C31 2C33 2C34 2C38 2C39 2C3A 2C58 2C59 2C77 2C79 2D04
2D05 2D18 2D19 2D30 2D39 2D58 2D59 2D79 2D83 2D98 2DB8 2DB9 2DD8 2DF9 2E03
2E06 2E12 2F04 2F06 2F18 2F38 2F58
"

function nullmoth_show_notice() {
  echo -e "\n${BOLD}${NULLMOTH_NOTICE_TITLE}${CL}\n"
  echo "$NULLMOTH_NOTICE_BODY"
  echo ""
}

# Echo the notice, then succeed only when the user accepted: either the
# --accept-nullmoth-risk flag ($1 = yes) or typing the phrase on a terminal.
function nullmoth_acknowledge() {
  local flag="$1" reply
  nullmoth_show_notice
  [ "$flag" == "yes" ] && return 0
  if [ ! -t 0 ]; then
    echo "Nothing was changed. Re-run with --accept-nullmoth-risk to accept the notice above."
    return 1
  fi
  echo "$NULLMOTH_ACK_LABEL"
  read -r -p "Type '${NULLMOTH_ACK_PHRASE}' to continue: " reply
  [ "$reply" == "$NULLMOTH_ACK_PHRASE" ] || { echo "Nothing was changed."; return 1; }
}

function nullmoth_supported_device() {
  local id="$1" hex
  (( id >= 0x1E00 )) && return 0
  hex=$(printf '%04X' "$id")
  [[ " ${NULLMOTH_SUPPORTED_IDS//$'\n'/ } " == *" $hex "* ]]
}

# macOS release from the description (written by this installer), then the VM name.
function nullmoth_detect_macos() {
  local cfg="$1" key hit
  for key in description name; do
    hit=$(sed -n "s/^${key}: //p" <<<"$cfg" | sed 's/%20/ /g' | grep -oiE 'ventura|sonoma|sequoia|tahoe' | head -1 || true)
    [ -n "$hit" ] && { echo "${hit,,}"; return 0; }
  done
  return 1
}

# Mount the VM's ide0 (OpenCore) disk, run the shared patcher, unmount.
# $1 vmid, $2 attach|detach|restore, $3 AMFIPass zip (attach only).
function nullmoth_oc_patch() {
  local nkept=0 vmid="$1" action="$2" zip="${3:-}" vol path loop mnt rc=1 _i old_trap
  vol=$(qm config "$vmid" 2>/dev/null | sed -n 's/^ide0: \([^,]*\).*/\1/p')
  [ -n "$vol" ] || { echo "ERROR: no ide0 (OpenCore) disk found"; return 1; }
  path=$(pvesm path "$vol" 2>/dev/null) || return 1
  loop=$(losetup -fP --show "$path" 2>/dev/null) || return 1
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    partprobe "$loop" 2>/dev/null || true
    [ -b "${loop}p1" ] && break
    sleep 1
  done
  mnt=$(mktemp -d)
  old_trap=$(trap -p INT TERM)
  # shellcheck disable=SC2064
  trap "umount '$mnt' 2>/dev/null; losetup -d '$loop' 2>/dev/null; rmdir '$mnt' 2>/dev/null; exit 130" INT TERM
  if [ -b "${loop}p1" ] && mount "${loop}p1" "$mnt" 2>/dev/null; then
    nkept=$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)
    if AMFIPASS_ZIP="$zip" AMFIPASS_SHA256="${zip:+$NULLMOTH_AMFIPASS_SHA256}" python3 - "$mnt/EFI/OC" "$action" <<'NULLMOTH_PY'
"""Prepare (or restore) a VM's OpenCore config for the NullMoth NVIDIA driver.

Usage: oc_patch.py <EFI/OC directory> attach|detach|restore

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_nullmoth_parity.py diffs the two),
so both entry points edit the disk identically. Standard library only.

attach  Backs up config.plist to config.plist.pre-nullmoth (kept if it already
        exists), adds AMFIPass.kext right after Lilu, appends the NullMoth
        boot-args, blocks IONDRVSupport and makes sure the SMBIOS model is
        MacPro7,1. Environment AMFIPASS_ZIP names the pinned, already
        verified AMFIPass release zip to unpack into Kexts/.
detach  Puts config.plist.pre-nullmoth back byte for byte and removes it,
        together with the AMFIPass.kext directory (unless the restored config still lists it). Refuses (exit 3) when there is
        no backup: a VM that was never attached keeps whatever it has.
restore Like detach, but succeeds when there is nothing to restore (the attach
        never got that far, or an earlier undo already finished).

attach stores the SHA-256 of the config it wrote next to the backup. detach and
restore put the backup back only while config.plist still has that hash. When
the file changed since (the NullMoth 1401 app edits it, or the user did), the
changed config and the kext stay, the backup stays, and a KEPT line says so.
The config is always written to a temporary file and renamed into place.

Environment AMFIPASS_SHA256, when set, is checked against the zip right before
it is unpacked.

Exit codes: 0 ok, 2 refused (precondition not met, nothing changed),
3 nothing to restore.
"""
import glob
import hashlib
import os
import plistlib
import shutil
import sys
import time
import zipfile

BACKUP = 'config.plist.pre-nullmoth'
PATCHED_SHA = BACKUP + '.patched-sha256'
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
BOOT_ARGS = (
    'nvfb=1', 'nvaccel=1', 'nvfbheads=4', '-nvkmsnosmooth',
    'amfi_get_out_of_my_way=0x1', 'amfi=0x80', 'ipc_control_port_options=0',
    'gfxrst=4',
)
REMOVE_ARGS = ('nv_disable=1', '-wegnoegpu')
AMD_ARGS = ('agdpmod',)
CSR_REQUIRED = 0x0A43
IONDRV = 'com.apple.iokit.IONDRVSupport'
KEXT = 'AMFIPass.kext'
SMBIOS_MODEL = 'MacPro7,1'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def merge_boot_args(current):
    """Append the NullMoth args, drop the disabling ones, never duplicate."""
    new_keys = set(a.split('=', 1)[0] for a in BOOT_ARGS)
    kept = [
        t for t in current.split()
        if t not in REMOVE_ARGS and t.split('=', 1)[0] not in new_keys
    ]
    return ' '.join(kept + list(BOOT_ARGS))


def check_preconditions(cfg):
    add = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {})
    csr = add.get('csr-active-config', b'')
    value = int.from_bytes(bytes(csr), 'little')
    if value & CSR_REQUIRED != CSR_REQUIRED:
        refuse('csr-active-config is 0x%04X but NullMoth needs 0x%04X set. '
               'This tool\'s default is 0x0F67; was the OpenCore disk edited?'
               % (value, CSR_REQUIRED))
    model = cfg.get('Misc', {}).get('Security', {}).get('SecureBootModel')
    if model != 'Disabled':
        refuse('SecureBootModel is %r but NullMoth needs Disabled.' % (model,))


def add_kext_entry(cfg):
    kexts = cfg.setdefault('Kernel', {}).setdefault('Add', [])
    kexts[:] = [k for k in kexts if k.get('BundlePath') != KEXT]
    pos = 0
    for i, k in enumerate(kexts):
        if k.get('BundlePath') == 'Lilu.kext':
            pos = i + 1
    kexts.insert(pos, {
        'Arch': 'Any', 'BundlePath': KEXT,
        'Comment': 'NullMoth: AMFI bypass', 'Enabled': True,
        'ExecutablePath': 'Contents/MacOS/AMFIPass', 'MaxKernel': '',
        'MinKernel': '', 'PlistPath': 'Contents/Info.plist',
    })


def add_block_entry(cfg):
    block = cfg.setdefault('Kernel', {}).setdefault('Block', [])
    block[:] = [b for b in block if b.get('Identifier') != IONDRV]
    block.append({
        'Arch': 'Any', 'Comment': 'NullMoth: keep IONDRVSupport out',
        'Enabled': True, 'Identifier': IONDRV, 'MaxKernel': '',
        'MinKernel': '', 'Strategy': 'Exclude',
    })


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def unpack_kext(zip_path, oc_dir):
    dest = os.path.join(oc_dir, 'Kexts')
    expected = os.environ.get('AMFIPASS_SHA256', '')
    if expected and file_sha(zip_path) != expected:
        refuse('the AMFIPass zip does not match its pinned SHA-256; it changed after it was verified.')
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist()
                   if i.filename.startswith(KEXT + '/') and not i.is_dir()]
        for info in members:
            if info.filename.startswith('/') or '..' in info.filename.split('/'):
                refuse('unsafe archive member: ' + info.filename)
        if not members:
            refuse('no ' + KEXT + ' inside ' + zip_path)
        # Validated first: a refused archive must not remove a working kext.
        shutil.rmtree(os.path.join(dest, KEXT), ignore_errors=True)
        for info in members:
            out = os.path.join(dest, info.filename)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, 'wb') as dst:
                shutil.copyfileobj(src, dst)


def write_plist(cfg, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        plistlib.dump(cfg, f)
    # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
    with open(tmp, 'rb') as f:
        data = f.read()
    for tag in ('array', 'dict', 'data'):
        data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    check_preconditions(cfg)
    args = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {}).get('boot-args', '')
    amd = [t for t in args.split() if t.split('=', 1)[0] in AMD_ARGS]
    if amd:
        refuse('config.plist still has the AMD GPU boot-args (' + ' '.join(amd) + '). Undo the AMD GPU setup '
               'first (gpu-setup --undo). If that keeps your config, put back the original from the '
               'config.plist.pre-gpu.kept-* file on the OpenCore disk, or remove those boot-args by hand.')
    zip_path = os.environ.get('AMFIPASS_ZIP', '')
    if zip_path:
        unpack_kext(zip_path, oc_dir)
    elif not os.path.isdir(os.path.join(oc_dir, 'Kexts', KEXT)):
        refuse('AMFIPASS_ZIP is not set and ' + KEXT + ' is not on the disk.')
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    add_kext_entry(cfg)
    add_block_entry(cfg)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''))
    generic = cfg.setdefault('PlatformInfo', {}).setdefault('Generic', {})
    if generic.get('SystemProductName') != SMBIOS_MODEL:
        generic['SystemProductName'] = SMBIOS_MODEL
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('NullMoth OpenCore changes applied (backup: ' + BACKUP + ')\n')


def references_kext(path):
    """Whether config.plist still lists AMFIPass.kext (unreadable counts as yes: keep the kext)."""
    try:
        with open(path, 'rb') as f:
            cfg = plistlib.load(f)
        return any(k.get('BundlePath') == KEXT for k in cfg.get('Kernel', {}).get('Add', []))
    except Exception:
        return True


def detach(oc_dir, soft=False):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    if not os.path.exists(backup):
        if soft and (not os.path.isdir(os.path.join(oc_dir, 'Kexts', KEXT))
                     or glob.glob(backup + '.kept-*')):
            sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
            return
        refuse('no ' + BACKUP + ' found, nothing to restore.', 3)
    if os.path.exists(stamp):
        with open(stamp) as f:
            wrote = f.read().strip()
        if os.path.exists(path) and file_sha(path) != wrote:
            kept = backup + '.kept-' + time.strftime('%Y%m%d%H%M%S')
            os.replace(backup, kept)
            os.remove(stamp)
            sys.stderr.write(
                'KEPT: config.plist was changed after NullMoth was prepared, so your version stays (and so does '
                + KEXT + ', which it may use). The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
    os.replace(backup, path)
    if os.path.exists(stamp):
        os.remove(stamp)
    if not references_kext(path):
        shutil.rmtree(os.path.join(oc_dir, 'Kexts', KEXT), ignore_errors=True)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    if len(argv) != 3 or argv[2] not in ('attach', 'detach', 'restore'):
        refuse('usage: oc_patch.py <EFI/OC dir> attach|detach|restore', 64)
    if argv[2] == 'attach':
        attach(argv[1])
    else:
        detach(argv[1], soft=argv[2] == 'restore')


if __name__ == '__main__':
    main(sys.argv)
NULLMOTH_PY
then
      rc=0
    fi
    [ "$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)" -le "$nkept" ] || GPU_OC_KEPT=$((${GPU_OC_KEPT:-0} + 1))
    sync
    umount "$mnt" 2>/dev/null || true
  fi
  losetup -d "$loop" 2>/dev/null || true
  rmdir "$mnt" 2>/dev/null || true
  trap - INT TERM
  [ -z "$old_trap" ] || eval "$old_trap"
  return "$rc"
}

# Download the pinned AMFIPass zip into $1 and verify its SHA-256. A mismatch
# is the tamper signal: stop. Prints the zip path.
function nullmoth_fetch_amfipass() {
  local dir="$1" dest
  dest="$dir/${NULLMOTH_AMFIPASS_URL##*/}"
  mkdir -p "$dir" || return 1
  if ! { [ -f "$dest" ] && echo "$NULLMOTH_AMFIPASS_SHA256  $dest" | sha256sum -c --status 2>/dev/null; }; then
    rm -f "$dest"
    curl -fsSL --retry 3 --max-time 600 -o "$dest" "$NULLMOTH_AMFIPASS_URL" || { rm -f "$dest"; return 1; }
    echo "$NULLMOTH_AMFIPASS_SHA256  $dest" | sha256sum -c --status || {
      rm -f "$dest"
      msg_error "Checksum mismatch for AMFIPass. The upstream file changed or the download was tampered with. Aborting."
      exit 1
    }
  fi
  echo "$dest"
}

function nullmoth_print_next_steps() {
  echo -e "\n${INFO}${YW}NullMoth next steps:${CL}"
  echo "  1. Start the VM. Once the NullMoth driver loads, the Proxmox console goes black (the driver takes the display"
  echo "     even with vga std). To get a desktop, connect a monitor or an HDMI/DisplayPort dummy plug to the card;"
  echo "     Screen Sharing then mirrors that display. SSH always works."
  echo "  2. Download the NullMoth release yourself from ${NULLMOTH_URL}/releases and check it with"
  echo "     shasum -a 256 -c SHA256SUMS, then run sudo ./install.sh in the guest."
  echo "  3. Approve the extensions in System Settings > Privacy & Security, then reboot the guest."
  echo "  4. Verify with: kmutil showloaded --list-only | grep -i nullmoth. Expect 4 or more lines"
  echo "     (NVRM, NVAccel, NVRMFB, NVRMAGDC, newer releases add more)."
  echo "  5. Turn off system sleep in the guest: sudo pmset -a sleep 0"
  echo "  6. Escape hatch: add -nvoff to the boot-args, or run gpu-setup --undo on the host."
  echo "  7. Warning: the NullMoth 1401 app edits the OpenCore config itself. Rebuilding the OpenCore"
  echo "     disk with this tool later (or running gpu-setup --undo) wipes those edits."
}

# Required in the guest BEFORE the card is attached: once the driver loads, the Proxmox console goes black.
NULLMOTH_GUEST_PREP_TITLE="Before you attach (required): get a way into macOS that does not need the Proxmox console"
NULLMOTH_GUEST_PREP_CONFIRM="Remote Login and Screen Sharing are on, I can reach the VM over the network, and a monitor or dummy plug is connected to the card."
function nullmoth_print_guest_prep() {
  echo "${NULLMOTH_GUEST_PREP_TITLE}:"
  echo "  1. In macOS open System Settings > General > Sharing and turn on Remote Login and Screen Sharing."
  echo "  2. Note the VM's IP address (shown in the Sharing pane, or in your router)."
  echo "  3. You will connect with ssh <user>@<ip>, or with any VNC client pointed at the VM's IP"
  echo "     (from a Mac: open vnc://<ip>)."
  echo "  4. If Remote Login will not switch on from the command line (systemsetup needs Full Disk Access), run:"
  echo "     sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist"
  echo "  5. Connect a monitor or an HDMI/DisplayPort dummy plug to the card. Without one the card has no display, so the"
  echo "     Proxmox console and Screen Sharing both stay black. Screen Sharing mirrors the display on the card; SSH always works."
}

function nullmoth_print_findings() {
  local line
  for line in "${GPU_ISSUES[@]}"; do echo -e "${CROSS}${RD}REFUSED: ${line}${CL}"; done
  for line in "${GPU_HINTS[@]}"; do echo "  $line"; done
  for line in "${GPU_WARNS[@]}"; do echo -e "${INFO}${YW}WARN: ${line}${CL}"; done
}

# Shared VM preflight. $1 vmid; sets NM_CFG.
function nullmoth_vm_check() {
  local vmid="$1" status
  NM_CFG=""
  status=$(qm status "$vmid" 2>/dev/null) || { GPU_ISSUES+=("VM $vmid not found."); return 1; }
  NM_CFG=$(qm config "$vmid" 2>/dev/null)
  grep -q '^args:.*isa-applesmc' <<<"$NM_CFG" || \
    GPU_ISSUES+=("VM $vmid is not a macOS VM made by this tool (no isa-applesmc in its args).")
  if grep -q 'status: running' <<<"$status"; then
    GPU_ISSUES+=("VM $vmid is running. Stop it first: qm stop $vmid")
  fi
}

# `osx-proxmox-next.sh nvidia-attach <vmid> <bdf> [--accept-nullmoth-risk] [--dry-run] [--macos <release>]`
function nvidia_attach() {
  local vmid="${1:-}" gpu="${2:-}" accept=no dry=no macos="" confirmed=no yes=no bdf args zip new_args version line
  shift 2 2>/dev/null || shift $#
  while [ $# -gt 0 ]; do
    case "$1" in
      --accept-nullmoth-risk) accept=yes ;;
      --yes|-y) yes=yes ;;
      --guest-confirmed) confirmed=yes ;;  # internal: the walkthrough already asked
      --dry-run) dry=yes ;;
      --macos) macos="${2:-}"; shift ;;
      *) msg_error "Unknown option: $1"; return 1 ;;
    esac
    shift
  done
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  [[ "$vmid" =~ ^[0-9]+$ ]] && bdf=$(gpu_normalize_bdf "$gpu") || {
    msg_error "Usage: osx-proxmox-next.sh nvidia-attach <vmid> <pci-address> [--accept-nullmoth-risk] [--yes] [--dry-run]"; return 1; }
  if [ "$dry" == "yes" ]; then nullmoth_show_notice; else nullmoth_acknowledge "$accept" || return 1; fi
  echo
  nullmoth_print_guest_prep
  echo
  if [ "$dry" != "yes" ] && [ "$confirmed" != "yes" ]; then
    # --yes answers this question only; the NullMoth acknowledgement above is never answered by it.
    local GPU_YES="$yes"
    gpu_ui_yesno "Before you attach" "${NULLMOTH_GUEST_PREP_CONFIRM}" || { msg_error "Nothing was changed. Turn Remote Login and Screen Sharing on in macOS first, then run again (--yes answers this question)."; return 2; }
  fi

  nullmoth_vm_check "$vmid" || true
  if [ -n "$NM_CFG" ]; then
    grep -q '^ide2:' <<<"$NM_CFG" && GPU_ISSUES+=("VM $vmid still has the recovery disk attached, so the install is not finished. Finish it, run post-install, then attach the card.")
    version="${macos,,}"
    [ -n "$version" ] || version=$(nullmoth_detect_macos "$NM_CFG") || version=""
    if [ -z "$version" ]; then
      GPU_ISSUES+=("Cannot tell which macOS this VM runs. Pass the version explicitly (--macos sequoia or tahoe).")
    elif [ "$version" != "sequoia" ] && [ "$version" != "tahoe" ]; then
      GPU_ISSUES+=("NullMoth needs macOS 15.5 Sequoia or newer (this VM is ${version}).")
    elif [ "$version" == "sequoia" ]; then
      GPU_WARNS+=("The guest must run macOS 15.5 or newer. Older Sequoia builds are not supported.")
    fi
    local n_before=${#GPU_ISSUES[@]}
    gpu_check "$bdf" 4318 nullmoth_supported_device 'Device 10de:%s is older than Turing. NullMoth supports RTX 20 series and newer only.'
    local w gpu_ok=yes
    [ ${#GPU_ISSUES[@]} -eq $n_before ] || gpu_ok=no
    for w in "${!GPU_WARNS[@]}"; do
      [[ "${GPU_WARNS[$w]}" == BAR1* ]] && GPU_WARNS[$w]+=" NullMoth's one Proxmox report (nullmoth issue #275) ran at 256 MiB."
    done
    local hp0
    hp0=$(sed -n 's/^hostpci0: //p' <<<"$NM_CFG")
    if [ -n "$hp0" ] && ! grep -qE "^(host=)?(0000:)?${bdf#0000:}(\.[0-7])?(,|;|\$)" <<<"$hp0"; then
      GPU_ISSUES+=("hostpci0 is already in use on this VM (${hp0}). Remove it first or run nvidia-detach.")
    fi
    if [ "$gpu_ok" == "yes" ]; then
      while read -r line; do
        [ -z "$line" ] && continue
        if [ "${line#* }" == "running" ]; then
          GPU_ISSUES+=("The card is in use by running VM ${line%% *}.")
        else
          GPU_WARNS+=("Also assigned to VM ${line%% *} (stopped). Only one of them can run at a time; Proxmox refuses to start the second.")
        fi
      done < <(gpu_users "$vmid" "$bdf")
    fi
  fi
  nullmoth_print_findings
  args=$(sed -n 's/^args: //p' <<<"$NM_CFG")
  new_args=$(gpu_with_mmio_arg "$args")
  if [ "$dry" == "yes" ]; then
    echo -e "\nDRY RUN - nothing is changed. Plan:"
    echo "  1. Verify VM $vmid is stopped"
    echo "  2. Prepare OpenCore for NullMoth on the ide0 disk (backup config.plist.pre-nullmoth, AMFIPass.kext, boot-args, IONDRVSupport block)"
    echo "  3. qm set $vmid --hostpci0 ${bdf},pcie=0"
    [ "$new_args" == "$args" ] || echo "  4. qm set $vmid --args '$new_args'"
    [ ${#GPU_ISSUES[@]} -eq 0 ]; return
  fi
  [ ${#GPU_ISSUES[@]} -eq 0 ] || return 1

  zip=$(nullmoth_fetch_amfipass "${NULLMOTH_CACHE_DIR:-/var/lib/vz/template/iso}") || { msg_error "Could not download AMFIPass"; return 1; }
  qm status "$vmid" 2>/dev/null | grep -q 'status: stopped' || { msg_error "VM $vmid is no longer stopped"; return 1; }
  gpu_snapshot "$vmid" >/dev/null || { msg_error "Could not save the VM config snapshot"; return 1; }
  # The record goes first: it only writes a sidecar file, and a failure later on still finds it.
  gpu_record_write "$vmid" "$bdf" nvidia "${bdf},pcie=0" "$GPU_MMIO_ARG" no nullmoth || { msg_error "Could not record the original VM settings"; return 1; }
  if nullmoth_oc_patch "$vmid" attach "$zip"; then
    msg_ok "OpenCore prepared for NullMoth (backup: config.plist.pre-nullmoth)"
  else
    msg_error "Could not prepare the OpenCore disk; the VM was not changed"; return 1
  fi
  qm set "$vmid" --hostpci0 "${bdf},pcie=0" >/dev/null || { msg_error "qm set --hostpci0 failed"; return 1; }
  msg_ok "Attached ${bdf} (all functions) as hostpci0"
  if [ "$new_args" != "$args" ]; then
    qm set "$vmid" --args "$new_args" >/dev/null || { msg_error "qm set --args failed"; return 1; }
    msg_ok "Added the 64-bit MMIO window to args"
  fi
  nullmoth_print_next_steps
}

# `osx-proxmox-next.sh nvidia-detach <vmid> [--dry-run]`
function nvidia_detach() {
  local vmid="${1:-}" dry=no stop=no args new_args
  shift 1 2>/dev/null || true
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=yes ;;
      --stop-vm) stop=yes ;;
      *) msg_error "Unknown option: $1"; return 1 ;;
    esac
    shift
  done
  [[ "$vmid" =~ ^[0-9]+$ ]] || { msg_error "Usage: osx-proxmox-next.sh nvidia-detach <vmid> [--dry-run]"; return 1; }
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  nullmoth_vm_check "$vmid" || true
  nullmoth_print_findings
  [ ${#GPU_ISSUES[@]} -eq 0 ] || return 1
  # Attached by this version: put the VM back exactly as it was (shared with gpu-setup --undo).
  if gpu_record_load "$vmid"; then
    gpu_detach_record "$vmid" "$dry" "$stop" || return
    [ "$dry" == "yes" ] || gpu_forget_vm "$vmid"
    return 0
  fi
  args=$(sed -n 's/^args: //p' <<<"$NM_CFG")
  new_args=$(gpu_without_mmio_arg "$args")
  if [ "$dry" == "yes" ]; then
    echo -e "\nDRY RUN - nothing is changed. Plan:"
    echo "  - Restore the previous OpenCore config from config.plist.pre-nullmoth (refuses if it is missing)"
    grep -q '^hostpci0:' <<<"$NM_CFG" && echo "  - qm set $vmid --delete hostpci0"
    [ "$new_args" == "$args" ] || echo "  - qm set $vmid --args '$new_args'"
    return 0
  fi
  # OpenCore first: with no backup it refuses and the VM keeps its hostpci0 and args.
  if nullmoth_oc_patch "$vmid" detach; then
    msg_ok "Restored the previous OpenCore config"
  else
    msg_error "Could not restore the OpenCore config (no config.plist.pre-nullmoth on the ide0 disk?). The VM was not changed."; return 1
  fi
  if grep -q '^hostpci0:' <<<"$NM_CFG"; then
    qm set "$vmid" --delete hostpci0 >/dev/null || { msg_error "qm set --delete hostpci0 failed"; return 1; }
    msg_ok "Removed hostpci0"
  fi
  if [ "$new_args" != "$args" ]; then
    if [ -n "$new_args" ]; then qm set "$vmid" --args "$new_args" >/dev/null; else qm set "$vmid" --delete args >/dev/null; fi || { msg_error "qm set --args failed"; return 1; }
    msg_ok "Removed the 64-bit MMIO window from args"
  fi
}
# ── NullMoth NVIDIA GPU attach (beta) end ──

# ── GPU walkthrough begin ──
# Bash twin of gpu.py, amd_host.py, gpu_attach.py, gpu_setup.py and gpu_setup_cli.py.
# The standalone engines (host_setup.py, bios_guide.py), their data files and the
# OpenCore boot-args patcher are embedded verbatim below; tests/test_gpu_parity.py
# diffs every copy against the file in the Python package, and
# scripts/embed_gpu_engines.py refreshes them. Change both sides together.
GPU_STATE_DIR="${GPU_STATE_DIR:-/var/lib/osx-proxmox-next}"
GPU_DOCS_ROOT="https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/"
GPU_RESUME_CMD='bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/scripts/bash/osx-proxmox-next.sh)" -- gpu-setup'
GPU_HOTPLUG_ARG="-global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off"
GPU_AMD_GENERIC_REASON="This AMD GPU has no Apple driver (APUs and cards newer than the RX 6000 series are not supported)."
GPU_AMD_VEGA20_WARNING="Radeon VII / Pro VII: stopping and starting a VM that holds this card without rebooting the host has corrupted host storage on Proxmox 9 with vendor-reset (vendor-reset issue 112). Reboot the host between VM runs and do not rely on automatic restarts."
GPU_PRE_TURING_REASON="Older than the RTX 20 series: NVIDIA's macOS drivers stopped at High Sierra and NullMoth needs Turing or newer."

# ── GPU embedded: host_setup.py begin ──
IFS= read -r -d '' GPU_EMBED_HOST_SETUP <<'GPU_EMBED_HOST_SETUP_EOF' || true
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
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = "/var/lib/osx-proxmox-next"
STATE_FILE = STATE_DIR + "/gpu-setup.json"
BACKUP_ROOT = STATE_DIR + "/backups"
LOGIN_HINT = "/etc/profile.d/osx-proxmox-next-gpu.sh"
PBT_UUIDS = "/etc/kernel/proxmox-boot-uuids"
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


# ── device identity ──────────────────────────────────────────────────
#
# The saved selection is (address, vendor:device, subsystem ids, PCI class). Every step that
# reads or writes host or VM state checks it again, because PCIe addresses move when a card
# changes slot, a BIOS option re-enumerates the buses, or another device is added.

VENDOR_IDS = {"amd": 0x1002, "nvidia": 0x10DE, "intel": 0x8086}
_IDENTITY_KEYS = ("vendor", "device", "subsys_vendor", "subsys_device", "class")
_FN_BDF_RE = re.compile(r"^(?:([0-9a-fA-F]{4}):)?([0-9a-fA-F]{2}):([0-9a-fA-F]{2})(?:\.([0-7]))?$")


def norm_bdf(raw):
    """domain:bus:slot (lowercase, function dropped) or None."""
    m = _FN_BDF_RE.match((raw or "").strip())
    return "%s:%s:%s" % ((m.group(1) or "0000").lower(), m.group(2).lower(), m.group(3).lower()) if m else None


def identity_of(root, bdf):
    """What sits at function 0 of *bdf* now, or None when nothing does."""
    base = _p(root, "/sys/bus/pci/devices/%s.0" % bdf)
    if not base.exists():
        return None
    return {"bdf": bdf, "vendor": _hex(base / "vendor"), "device": _hex(base / "device"),
            "subsys_vendor": _hex(base / "subsystem_vendor"), "subsys_device": _hex(base / "subsystem_device"),
            "class": _hex(base / "class")}


def changed_card_message(bdf):
    return "The card at %s is now a different device. Your PCI layout changed. Run the scan again." % bdf


def check_identity(root, saved):
    """"" when the device at the saved address is still the saved card, else a plain explanation."""
    if not saved or not saved.get("bdf"):
        return ""
    now = identity_of(root, saved["bdf"])
    if now is None or (now["class"] or 0) >> 16 != 0x03:
        return changed_card_message(saved["bdf"])
    for key in _IDENTITY_KEYS:
        if key in saved and saved[key] != now[key]:
            return changed_card_message(saved["bdf"])
    return ""


def bindable(fn, vendor):
    """True for a function this tool may hand to vfio-pci: the card's display, audio, USB and UCSI
    functions of the expected vendor. Storage (0x01) and network (0x02) are never bindable."""
    cls = fn.get("class") or 0
    return fn.get("vendor") == VENDOR_IDS.get(vendor) and (cls >> 16 == 0x03 or cls >> 8 in (0x0403, 0x0C03, 0x0C80))


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
            "subsys_vendor": _hex(dev / "subsystem_vendor"),
            "subsys_device": _hex(dev / "subsystem_device"),
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
    if target["vendor"] == "intel" and facts["iommu_groups"] > 0:
        return args  # the iGPU only needs the IOMMU to be on, however it got switched on
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
        if _p(root, PBT_UUIDS).exists():  # BIOS/ZFS hosts boot GRUB from copies on the ESPs
            plan["commands"].append({"argv": ["proxmox-boot-tool", "refresh"],
                                     "reason": "Copy the GRUB config to the boot partitions"})
            plan["refresh"].append("pbt")
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
            "dark once the boot loader hands over. Use SSH or the web UI from then on; both keep working.")


# ── plan helpers ─────────────────────────────────────────────────────

def _add_change(plan, root, path, content, reason, mode=0o644):
    old = _read(_p(root, path))
    if old == content:
        return
    plan["changes"].append({"path": path, "old": old, "new": content, "reason": reason, "mode": mode})


def new_plan():
    return {"changes": [], "commands": [], "notes": [], "warnings": [], "hard_warnings": [], "refusals": [],
            "manual": [], "skipped": [], "refresh": [], "reboot_required": False, "binding": "", "initramfs": False,
            "identity": None, "optional": False}


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


def _guard_target(target, plan):
    """Refuse unless the target is a display card with only bindable functions. True when it is."""
    if target.get("mismatch"):
        plan["refusals"].append(target["mismatch"])
        return False
    fns = target.get("functions") or []
    fn0 = next((f for f in fns if f["bdf"].endswith(".0")), None)
    if fn0 is None or (fn0.get("class") or 0) >> 16 != 0x03 or fn0.get("vendor") != VENDOR_IDS.get(target["vendor"]):
        plan["refusals"].append(
            "The device at %s.0 is not a %s graphics card. Your PCI layout changed. Run the scan again."
            % (target["bdf"], {"amd": "AMD", "nvidia": "NVIDIA", "intel": "Intel"}.get(target["vendor"], "supported")))
        return False
    for fn in fns:
        if (fn.get("class") or 0) >> 16 in (0x01, 0x02):
            plan["refusals"].append(
                "%s is a storage or network device and is never bound to vfio-pci. Run the scan again." % fn["bdf"])
            return False
    return True


def _bind_entries(text):
    """(bdf, vendor, device, class prefix, subsystem vendor, subsystem device) rows of an existing bind
    script written by this tool. "-" means the subsystem ids were not recorded (an older script)."""
    return [(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5) or "-", m.group(6) or "-")
            for m in re.finditer(
                r"^bind (\S+) (0x[0-9a-f]{4}) (0x[0-9a-f]{4}) (0x[0-9a-f]+)(?: (0x[0-9a-f]{4}|-) (0x[0-9a-f]{4}|-))?$",
                text or "", re.M)]


def _subsys(value):
    return "0x%04x" % value if value is not None else "-"


_CMD_IDS_RE = re.compile(r"^vfio[-_]pci\.ids=([0-9A-Fa-f:,]+)$")
_SOFTDEP_RE = re.compile(r"^\s*softdep\s+(\S+)\s+pre:(.*)$")


def _persistent_binding(facts, target, fns, ids, root, by_id=True, ids_only=False, ordered=True):
    """What already binds this card to vfio-pci at boot, as a short phrase, or "" when nothing we can find does.
    Looks at: modprobe.d `options vfio-pci ids=` covering every id, a driver_override in an initramfs script or
    udev rule that names the card, `vfio-pci.ids=` on the live or configured kernel command line, and
    Softdep lines only order module loading; they bind nothing, so they are handled apart (_softdeps_listed).
    *ordered* is False when a host driver can claim the card and nothing orders vfio-pci first: ids alone then
    prove nothing, only an early per-address bind that covers every function does."""
    d = _p(root, "/etc/modprobe.d")
    confs = sorted(d.glob("*.conf")) if d.is_dir() else []
    have = set()
    for f in confs:
        have.update(_live_ids(_read(f) or ""))
    if ordered and by_id and ids and all(i in have for i in ids):
        return "vfio-pci ids in /etc/modprobe.d"
    cmd_ids = set()
    # what the boot loader will pass next time; the live command line only when that cannot be read
    nxt = configured_cmdline(facts.get("bootloader"), root) + _tokens(_read(_p(root, KERNEL_CMDLINE)))
    for tok in nxt or _tokens(facts.get("cmdline")):
        m = _CMD_IDS_RE.match(tok)
        if m:
            cmd_ids.update(i.lower() for i in m.group(1).split(","))
    if ordered and by_id and ids and all(i in cmd_ids for i in ids):
        return "vfio-pci.ids on the kernel command line"
    if ids_only:
        return ""
    own = _bind_entries(_read(_p(root, BIND_SCRIPT)))
    if fns and all(any(r[0].lower() == f["bdf"].lower() and r[1] == "0x%04x" % f["vendor"]
                       and r[2] == "0x%04x" % f["device"] for r in own) for f in fns):
        return "a driver_override script (%s)" % Path(BIND_SCRIPT).name
    scripts = []
    for sub in ("/etc/initramfs-tools/scripts", "/etc/udev/rules.d"):
        top = _p(root, sub)
        if top.is_dir():
            scripts += [f for f in sorted(top.rglob("*")) if f.is_file()]
    for f in scripts:
        if "/" + f.relative_to(Path(root)).as_posix() == BIND_SCRIPT:
            continue
        text = (_read(f) or "").lower()
        if "driver_override" in text and "vfio-pci" in text and fns and all(x["bdf"].lower() in text for x in fns):
            return "a driver_override script (%s)" % f.name
    return ""


_BL_RE = re.compile(r"^\s*blacklist\s+(\S+)")


def _softdeps_listed(target, root):
    """True when modprobe.d carries `softdep <driver> pre: vfio-pci` for every driver that could claim the card."""
    d = _p(root, "/etc/modprobe.d")
    softdeps = set()
    for f in (sorted(d.glob("*.conf")) if d.is_dir() else []):
        for line in (_read(f) or "").splitlines():
            m = _SOFTDEP_RE.match(line.split("#", 1)[0])
            if m and "vfio-pci" in m.group(2).replace("_", "-").split():
                softdeps.add(m.group(1).replace("-", "_"))
    return all(n.replace("-", "_") in softdeps for n in _softdeps(target))


def _claiming_drivers(root):
    """NVIDIA host drivers that are installed and not blacklisted, so could take the card at boot.
    A kernel without a readable modules.dep counts as "installed" (unknown is not safe)."""
    d = _p(root, "/etc/modprobe.d")
    blocked = set()
    for f in (sorted(d.glob("*.conf")) if d.is_dir() else []):
        for line in (_read(f) or "").splitlines():
            m = _BL_RE.match(line.split("#", 1)[0])
            if m:
                blocked.add(m.group(1).replace("-", "_"))
    deps = sorted(_p(root, "/lib/modules").glob("*/modules.dep")) if _p(root, "/lib/modules").is_dir() else []
    text = "".join(_read(f) or "" for f in deps)
    found = []
    for name in ("nouveau", "nvidia"):
        if name in blocked:
            continue
        if deps and not re.search(r"(^|/)%s\.ko[^:/]*:" % name, text, re.M):
            continue
        found.append(name)
    return found


def _blacklisted(root, name):
    d = _p(root, "/etc/modprobe.d")
    for f in (sorted(d.glob("*.conf")) if d.is_dir() else []):
        for line in (_read(f) or "").splitlines():
            m = _BL_RE.match(line.split("#", 1)[0])
            if m and m.group(1).replace("-", "_") == name:
                return True
    return False


def plan_intel_binding(facts, target, root, plan):
    """The Intel iGPU: the host gives it up completely. i915 is blacklisted and vfio-pci binds the id at boot.
    No disable_vga (the legacy IGD mode needs the VGA decode), no softdeps (nothing else may load first)."""
    fns = [f for f in target["functions"] if bindable(f, "intel")]
    ids = []
    for fn in fns:
        ident = "%04x:%04x" % (fn["vendor"], fn["device"])
        if ident not in ids:
            ids.append(ident)
    drivers = {d["bdf"]: d.get("driver") for d in facts["pci"]}
    if fns and all(drivers.get(f["bdf"]) == "vfio-pci" for f in fns) and _blacklisted(root, "i915"):
        how = _persistent_binding(facts, target, fns, ids, root)
        if how:
            plan["skipped"].append("The iGPU is already held by vfio-pci and i915 is blacklisted (%s)" % how)
            return
    mine = {fn["bdf"] for fn in target["functions"]}
    twins = sorted(d["bdf"] for d in facts["pci"] if d["bdf"] not in mine and d["vendor"] is not None
                   and "%04x:%04x" % (d["vendor"], d["device"]) in ids)
    if twins:
        plan["refusals"].append(
            "Another device on this host has the same PCI id (%s). Binding by id would take it too." % ", ".join(twins))
        return
    existing = _existing_ids_lines(root)
    for path, text in existing:
        new = _merge_ids(text, ids)
        if new != text:
            _add_change(plan, root, path, new, "Add the iGPU's id to your existing vfio-pci line")
    earlier = _live_ids(_read(_p(root, MODPROBE_FILE)) or "")
    union = earlier + [i for i in ids if i not in earlier]
    body = HEADER + "blacklist i915\n" + ("" if existing else "options vfio-pci ids=%s\n" % ",".join(union))
    _add_change(plan, root, MODPROBE_FILE, body, "Give the iGPU to vfio-pci at boot (i915 is blacklisted)")
    plan["warnings"].append(
        "The host gives up its Intel graphics for good: the monitor on the host stays dark and the Proxmox "
        "console works only over the network. Make sure you can reach the web UI or SSH before you reboot.")
    plan["binding"] = "ids"


def plan_binding(facts, target, root, plan, opts=None):
    opts = opts or {}
    if target["vendor"] == "intel":
        plan_intel_binding(facts, target, root, plan)
        return
    fns = [f for f in target["functions"] if bindable(f, target["vendor"])]
    ids = []
    for fn in fns:
        ident = "%04x:%04x" % (fn["vendor"], fn["device"])
        if ident not in ids:
            ids.append(ident)
    drivers = {d["bdf"]: d.get("driver") for d in facts["pci"]}
    if fns and all(drivers.get(f["bdf"]) == "vfio-pci" for f in fns):
        mine_bdfs = {fn["bdf"] for fn in target["functions"]}
        has_twin = any(d["bdf"] not in mine_bdfs and d["vendor"] is not None
                       and "%04x:%04x" % (d["vendor"], d["device"]) in ids for d in facts["pci"])
        # an id-wide binding also takes an identical second card: that is the refusal further down, not "already OK"
        # a host driver that can claim the card makes ids alone worthless: vfio-pci must be ordered first
        claimers = _claiming_drivers(root) if target["vendor"] == "nvidia" else ["amdgpu", "radeon"]
        ordered = not claimers or _softdeps_listed(target, root)
        idwide = has_twin and bool(_persistent_binding(facts, target, fns, ids, root, ids_only=True))
        how = "" if idwide else _persistent_binding(facts, target, fns, ids, root, by_id=not has_twin,
                                                    ordered=ordered)
        if how:
            plan["skipped"].append("The card is already bound to vfio-pci and set to bind at boot (%s)" % how)
            return
        if not idwide and not claimers and _softdeps_listed(target, root):
            plan["skipped"].append("The card is already bound to vfio-pci and no host driver claims it at boot")
            plan["notes"].append("Proxmox hands the card to vfio-pci when the VM starts; no host driver claims it "
                                 "at boot.")
            return
        # a driver that can claim the card (and AMD's reset bug) needs the early binding: no optional skip
        if not idwide and not claimers and not opts.get("write_optional"):
            plan["optional"] = True
            plan["warnings"].append(
                "The card is bound to vfio-pci now, but nothing we can find binds it at boot; it may be lost "
                "after a reboot. Writing a boot-time binding is optional and skipped unless you ask for it.")
            return
    mine = {fn["bdf"] for fn in target["functions"]}
    twins = sorted(d["bdf"] for d in facts["pci"] if d["bdf"] not in mine and d["vendor"] is not None
                   and "%04x:%04x" % (d["vendor"], d["device"]) in ids)
    existing = _existing_ids_lines(root)
    earlier = _live_ids(_read(_p(root, MODPROBE_FILE)) or "")  # ids this tool bound for a card set up before
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
        keep = [i for i in earlier if i not in ids]
        head = HEADER + ("options vfio-pci ids=%s disable_vga=1\n" % ",".join(keep) if keep else "")
        _add_change(plan, root, MODPROBE_FILE, head + softdep, "Load vfio-pci before the GPU drivers")
        rows = _bind_entries(_read(_p(root, BIND_SCRIPT)))
        for fn in fns:
            row = (fn["bdf"], "0x%04x" % fn["vendor"], "0x%04x" % fn["device"], _class_prefix(fn),
                   _subsys(fn.get("subsys_vendor")), _subsys(fn.get("subsys_device")))
            rows = [r for r in rows if r[:3] != row[:3]]
            rows.append(row)
            same = [d["bdf"] for d in facts["pci"] if d["bdf"] not in mine and d.get("vendor") == fn["vendor"]
                    and d.get("device") == fn["device"] and d.get("subsys_vendor") == fn.get("subsys_vendor")
                    and d.get("subsys_device") == fn.get("subsys_device")]
            if same and fn["bdf"].endswith(".0"):
                plan["warnings"].append(
                    "%s has the same ids as %s, even the subsystem ids, so the card is told apart by its slot "
                    "only. Do not move either card to another slot." % (fn["bdf"][:-2], ", ".join(same)))
        _add_change(plan, root, BIND_SCRIPT, _bind_script(rows),
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
            union = earlier + [i for i in ids if i not in earlier]
            if len(union) > len(ids):
                plan["notes"].append("The ids of the card you set up before stay bound: " + ", ".join(earlier) + ".")
            body = HEADER + "options vfio-pci ids=%s disable_vga=1\n" % ",".join(union) + softdep
        elif merged_all:
            plan["notes"].append("The card's ids are already in your vfio-pci options.")
        _add_change(plan, root, MODPROBE_FILE, body, "Bind the card to vfio-pci before its driver loads")


def _class_prefix(fn):
    """Prefix of the sysfs class value the boot script insists on: 0x03 for the display, 0x0403 and so on."""
    cls = fn.get("class") or 0
    return "0x03" if cls >> 16 == 0x03 else "0x%04x" % (cls >> 8)


def _bind_script(rows):
    """Initramfs script. Each line re-checks vendor, device, class and subsystem ids at boot: an address that now
    holds a different device (a card moved, an NVMe added) is skipped, never bound."""
    lines = "\n".join("bind %s %s %s %s %s %s" % tuple(row) for row in rows)
    return (
        "#!/bin/sh\n" + HEADER +
        'PREREQ=""\nprereqs() { echo "$PREREQ"; }\ncase "$1" in prereqs) prereqs; exit 0 ;; esac\n'
        "# Mark this card's functions for vfio-pci before any GPU driver can claim them, but only when the\n"
        "# device at that address still has the vendor, device id, class and subsystem ids recorded at setup time.\n"
        "bind() {\n"
        '  d="${OSX_NEXT_SYSFS:-/sys}/bus/pci/devices/$1"\n'
        '  [ -r "$d/vendor" ] || return 0\n'
        '  read -r v < "$d/vendor"; read -r dev < "$d/device"; read -r c < "$d/class"\n'
        '  [ "$v" = "$2" ] && [ "$dev" = "$3" ] || return 0\n'
        '  case "$c" in "$4"*) ;; *) return 0 ;; esac\n'
        '  sv=""; sd=""\n'
        '  [ -r "$d/subsystem_vendor" ] && read -r sv < "$d/subsystem_vendor"\n'
        '  [ -r "$d/subsystem_device" ] && read -r sd < "$d/subsystem_device"\n'
        '  [ "$5" = "-" ] || [ "$sv" = "$5" ] || return 0\n'
        '  [ "$6" = "-" ] || [ "$sd" = "$6" ] || return 0\n'
        '  echo vfio-pci > "$d/driver_override" 2>/dev/null || true\n'
        "}\n"
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
    # A vendor-reset the user registered with dkms before is theirs: build for this kernel if needed, never remove it.
    code, known = run(["dkms", "status", "-m", "vendor-reset"])
    tag = "vendor-reset-existing" if code == 0 and known.strip() else "vendor-reset"
    if built:
        plan["skipped"].append("vendor-reset is already built for this kernel")
    else:
        kernel = facts["kernel"]
        plan["commands"].append({"argv": ["apt-get", "install", "-y", "dkms", "git", "proxmox-default-headers",
                                          "proxmox-headers-" + kernel], "reason": "Packages needed to build vendor-reset"})
        src = shlex.quote(VR_SRC)
        plan["commands"].append({"argv": ["bash", "-c",
            "[ -d %s/.git ] || git clone %s %s || exit 1; "
            "[ -z \"$(git -C %s status --porcelain)\" ] || { echo 'ERROR: %s has local changes, not building from it'; exit 1; }; "
            "git -C %s checkout -q %s || exit 1; "
            "[ \"$(git -C %s rev-parse HEAD)\" = %s ] || { echo 'ERROR: %s is not at the pinned commit'; exit 1; }"
            % (src, VR_REPO, src, src, VR_SRC, src, VR_SHA, src, VR_SHA, VR_SRC)],
            "reason": "Fetch vendor-reset at the pinned commit " + VR_SHA[:12]})
        plan["commands"].append({"argv": ["bash", "-c",
            "dkms status -m vendor-reset -v %s | grep -q . || dkms add -m vendor-reset -v %s; "
            "dkms install -m vendor-reset -v %s" % (VR_VERSION, VR_VERSION, VR_VERSION)],
            "reason": "Build and install the vendor-reset module", "tag": tag})
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
                "screen. Type 'I understand' to continue, and make sure you can reach this host over SSH first.")
        elif not facts["ssh"]:
            plan["warnings"].append("You are not on an SSH session. Make sure you can log in over the network "
                                    "before you reboot.")
        plan["warnings"].append("Rescue if the host will not boot: in the GRUB menu press 'e', delete the arguments "
                                "added by this tool from the linux line, press Ctrl-X, then run gpu-setup --undo.")
    else:
        plan["notes"].append("The chosen card is the console GPU. Another display adapter exists, so the host keeps "
                             "a screen only if the BIOS uses it as the primary display (see the BIOS step).")


def lxc_gpu_users(root="/"):
    """(container id, device node) for every container config that passes /dev/nvidia* or /dev/dri in."""
    d = _p(root, "/etc/pve/lxc")
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.conf")):
        if not f.stem.isdigit():
            continue
        nodes = []
        for line in (_read(f) or "").splitlines():
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"/dev/(nvidia\w*|dri)\b", line):
                node = "/dev/dri" if m.group(1) == "dri" else "/dev/nvidia"
                if node not in nodes:
                    nodes.append(node)
        out += [(int(f.stem), node) for node in nodes]
    return out


def plan_containers(target, root, plan):
    for ctid, node in lxc_gpu_users(root):
        if node == "/dev/nvidia" and target["vendor"] != "nvidia":
            continue
        plan["warnings"].append(
            "Container %d passes %s through. Once this card is bound to vfio-pci that container loses the GPU at "
            "the next boot (transcoding and other GPU work in it stops). Remove the device from its config first "
            "if you still need it there." % (ctid, node))


def plan_host(facts, target, opts=None, root="/", run=default_run):
    """Everything this host still needs for *target* (see module doc for the shape)."""
    opts = opts or {}
    plan = new_plan()
    plan["identity"] = target.get("identity")
    if not _guard_target(target, plan):
        return plan
    plan_console(facts, target, opts, plan)
    plan_kernel_args(facts, target, root, plan)
    if target["vendor"] == "intel":
        plan_binding(facts, target, root, plan, opts)
        if any("already held by vfio-pci" in line for line in plan["skipped"]):
            plan["skipped"].append("The VFIO modules are loaded (the iGPU is bound)")
        else:
            plan_modules(root, plan)
    else:
        plan_modules(root, plan)
        plan_binding(facts, target, root, plan, opts)
    plan_vendor_reset(facts, target, root, run, plan)
    plan_containers(target, root, plan)
    boot_files = (GRUB_DROPIN, KERNEL_CMDLINE)
    plan["initramfs"] = any(c["path"] not in boot_files for c in plan["changes"]) or \
        any(c.get("tag", "").startswith("vendor-reset") for c in plan["commands"])
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


def build_target(root, bdf, vendor, reset="none", identity=None):
    """The chosen card in the shape plan_host and verify_host want.

    *identity* is the saved selection (see check_identity). When the device at *bdf* no longer
    matches it the target carries ``mismatch`` and every step refuses to act on it."""
    devices = pci_devices(root)
    funcs = [{"bdf": d["bdf"], "vendor": d["vendor"], "device": d["device"], "class": d["class"],
              "subsys_vendor": d["subsys_vendor"], "subsys_device": d["subsys_device"]}
             for d in devices if d["bdf"].startswith(bdf + ".")]
    boot = any(d["boot_vga"] for d in devices if d["bdf"].startswith(bdf + "."))
    return {"vendor": vendor, "bdf": bdf, "boot_vga": boot, "reset": reset, "functions": funcs,
            "identity": identity, "mismatch": check_identity(root, identity)}


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


def _real(root, path):
    """The file behind *path*. A symlink is written through, never replaced, so it stays a link."""
    return Path(os.path.realpath(_p(root, path)))


def _write_file(root, path, content, mode):
    target = _real(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = target.stat()
        mode = stat.S_IMODE(st.st_mode)  # keep the mode of a file that already existed
    except OSError:
        st = None
    tmp = target.with_name(target.name + ".osx-tmp")
    try:
        tmp.write_text(content)
        os.chmod(tmp, mode)
        if st is not None:
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _restore(root, entry, check_drift=False):
    """Put one file back. Returns "" or the reason it was left alone."""
    target = _real(root, entry["path"])
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


_REFRESH = {"grub": ["update-grub"], "pbt": ["proxmox-boot-tool", "refresh"],
            "systemd-boot": ["proxmox-boot-tool", "refresh"],
            "initramfs": ["update-initramfs", "-u", "-k", "all"]}
_KINDS = ("grub", "pbt", "systemd-boot", "initramfs")


def _refresh_commands(kinds):
    return [_REFRESH[k] for k in _KINDS if k in kinds]


def _run_refreshes(kinds, state, run):
    """Rebuild boot config for *kinds*. Failures stay in state["pending_refresh"] so a later undo retries them."""
    errors = []
    pending = []
    for kind in _KINDS:
        if kind not in kinds:
            continue
        code, out = run(_REFRESH[kind], timeout=LONG_TIMEOUT)
        if code != 0:
            pending.append(kind)
            errors.append("%s failed: %s" % (" ".join(_REFRESH[kind]), out.strip()[-200:]))
    state["pending_refresh"] = pending
    return errors


def _ignore_sigint():
    """Ignore Ctrl-C while a rollback runs. Returns what to restore (None when it cannot be changed here)."""
    try:
        return signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # not the main thread
        return None


def apply_plan(plan, state, root="/", run=default_run, confirmed=False):
    """Write the plan. Returns {ok, error, rolled_back, reboot_required, backup_dir}."""
    if plan["refusals"]:
        return {"ok": False, "error": plan["refusals"][0], "rolled_back": False}
    if plan["hard_warnings"] and not confirmed:
        return {"ok": False, "error": "A hard warning needs a typed confirmation first.", "rolled_back": False}
    moved = check_identity(root, plan.get("identity"))
    if moved:
        return {"ok": False, "error": moved, "rolled_back": False}
    bad = [c["path"] for c in plan["changes"] if not _safe_path(c["path"])]
    if bad:
        return {"ok": False, "error": "Refusing to write outside /etc: " + ", ".join(bad), "rolled_back": False}
    stale = [c["path"] for c in plan["changes"] if _read(_real(root, c["path"])) != c["old"]]
    if stale:
        return {"ok": False, "rolled_back": False,
                "error": "%s changed after the preview was made, so nothing was written. Run the step again to "
                         "see the new preview." % ", ".join(stale)}
    stamp = _now()
    n = 1
    while _p(root, BACKUP_ROOT).joinpath(stamp).exists():
        n += 1
        stamp = "%s-%d" % (_now(), n)
    done = []
    try:
        for ch in plan["changes"]:
            target = _real(root, ch["path"])
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
        # Saved first: a second Ctrl-C during the slow rebuilds must still leave undo able to redo them.
        state["pending_refresh"] = list(plan["refresh"])
        previous = _ignore_sigint()
        try:
            save_state(state, root)
            for entry in reversed(done):
                _restore(root, entry)
            errors = _run_refreshes(set(plan["refresh"]), state, run)
            save_state(state, root)
        finally:
            if previous is not None:
                signal.signal(signal.SIGINT, previous)
        if not isinstance(exc, (OSError, RuntimeError)):
            raise
        extra = [e for e in errors if e not in str(exc)]
        return {"ok": False, "error": str(exc) + "".join(" " + e for e in extra), "rolled_back": True}
    return {"ok": True, "error": "", "rolled_back": False, "reboot_required": plan["reboot_required"],
            "backup_dir": str(_p(root, BACKUP_ROOT) / stamp)}


def undo_host(state, root="/", run=default_run):
    """Restore every file this tool changed, newest first. Safe to run twice; a failed rebuild is retried.

    A file somebody changed after this tool wrote it is kept as it is and reported."""
    kinds = set(state.get("pending_refresh", []))
    restored = []
    errors = []
    kept = []
    for entry in reversed(state["host_changes"]):
        if entry.get("reverted"):
            continue
        real = _real(root, entry["path"])
        if _safe_path(entry["path"]) and real.exists() and _sha(_read(real)) != entry.get("sha_after"):
            # Somebody changed it after this tool wrote it: their version stays and undo carries on.
            entry["reverted"] = True
            kept.append("%s was changed after this tool wrote it, so your version was kept (the original is in %s)."
                        % (entry["path"], entry.get("backup") or "no backup: it was a new file"))
            continue
        problem = _restore(root, entry)
        if problem:
            errors.append(problem)
            continue
        restored.append(entry["path"])
        if entry["path"] == GRUB_DROPIN:
            kinds.add("grub")
            if _p(root, PBT_UUIDS).exists():
                kinds.add("pbt")
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
    return {"ok": not errors, "restored": restored, "errors": errors, "kept": kept,
            "reboot_required": bool(restored)}


# ── verify ───────────────────────────────────────────────────────────

def _check(cid, label, status, detail="", fix=""):
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix}


def verify_host(target, root="/", run=default_run):
    """Post-reboot checks. status is ok, fail, warn or info; fix says where to go on a failure."""
    moved = check_identity(root, target.get("identity"))
    if moved:
        return [_check("identity", "The chosen card is still there", "fail", moved, "scan")]
    probe = new_plan()
    if not _guard_target(dict(target, mismatch=""), probe):
        return [_check("identity", "The chosen card is still there", "fail", probe["refusals"][0], "scan")]
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
             for f in target["functions"]
             if bindable(f, target["vendor"]) and devs.get(f["bdf"], {}).get("driver") != "vfio-pci"]
    out.append(_check("driver", "The card is held by vfio-pci", "ok" if not wrong else "fail",
                      "" if not wrong else "Not bound: " + ", ".join(wrong) + ". Go back to the host step and reboot after applying.",
                      "" if not wrong else "host"))
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
                      "Some guides suggest the pcie_acs_override kernel option to split the group. It only hides the "
                      "problem and can let the VM reach those devices, so this tool never uses it.")
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


# ── VM bookkeeping (shared by the Python front ends and the bash installer) ──

def parse_config(raw):
    """`qm config` text as a dict."""
    cfg = {}
    for line in (raw or "").splitlines():
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


def _global_re(arg):
    """Matches the arg with any value: ``-global k=v``, ``-machine k=v`` and ``-set k=v`` alike."""
    key = arg.split("=", 1)[0]
    return re.compile(r"\s+".join(re.escape(part) for part in key.split()) + r"=\S+")


def with_global_args(args, wanted):
    """*args* with each ``-global key=value`` set to the wanted value, never duplicated."""
    out = args
    for arg in wanted:
        rx = _global_re(arg)
        out = rx.sub(arg, out) if rx.search(out) else "%s %s" % (out.rstrip(), arg)
    return out.strip()


def without_global_args(args, wanted):
    out = args
    for arg in wanted:
        out = re.sub(r"\s*" + _global_re(arg).pattern, "", out)
    return out.strip()


def vm_fingerprint(cfg):
    """Identifies one VM across a VMID that was reused: its SMBIOS uuid, else its name."""
    m = re.search(r"uuid=([0-9a-fA-F-]+)", cfg.get("smbios1", ""))
    return m.group(1).lower() if m else cfg.get("name", "")


def record_vm_changed(record, cfg):
    """True when *record* was written for another VM than *cfg* (a deleted and re-created VMID)."""
    fp = record.get("fp", "")
    return bool(fp) and fp != vm_fingerprint(cfg)


def hostpci_key(value):
    """Order-free form of a hostpci value, so `pcie=1,0000:06:00` equals `0000:06:00,pcie=1`."""
    addrs, opts = set(), set()
    for tok in re.split(r"[,;]", value or ""):
        tok = tok.strip()
        if not tok:
            continue
        bare = tok[5:] if tok.startswith("host=") else tok
        if "=" not in bare:
            addrs.add(norm_bdf(bare) or bare)
        else:
            opts.add(bare)
    return (frozenset(addrs), frozenset(opts))


def make_record(vmid, bdf, vendor, cfg, hostpci, global_args, vga_none, oc):
    """What undo needs: the VM before the attach, and what the attach wrote."""
    args = cfg.get("args")
    added, replaced = [], {}
    for arg in global_args:
        old = _global_re(arg).search(args or "")
        if old is None:
            added.append(arg)
        elif old.group(0) != arg:
            replaced[arg] = old.group(0)
    return {"vmid": int(vmid), "bdf": bdf, "vendor": vendor, "hostpci0": cfg.get("hostpci0"), "args": args,
            "vga": cfg.get("vga"), "vga_changed": bool(vga_none), "oc": oc, "fp": vm_fingerprint(cfg),
            "after": {"hostpci0": hostpci, "args": with_global_args(args or "", global_args),
                      "vga": "none" if vga_none else None, "added_args": added, "replaced_args": replaced}}


def vm_restore(record, cfg):
    """Actions that put a VM back: {"actions": [{"op": "set"|"delete", "key", "value"}], "notes": [...]}.

    Only what the attach wrote is reverted. A field somebody changed afterwards keeps the user's
    value and is reported; an edited args line only loses the arguments this tool added."""
    actions, notes = [], []
    after = record.get("after")
    cur_hp, orig_hp = cfg.get("hostpci0"), record.get("hostpci0")
    if cur_hp == orig_hp:
        pass  # untouched (a refused or partial attach): nothing to undo, nothing to claim
    elif after is None or (cur_hp is not None and hostpci_key(cur_hp) == hostpci_key(after.get("hostpci0"))):
        if orig_hp:
            if cur_hp != orig_hp:
                actions.append({"op": "set", "key": "hostpci0", "value": orig_hp})
        elif cur_hp is not None:
            actions.append({"op": "delete", "key": "hostpci0"})
    elif cur_hp is not None:
        notes.append("hostpci0 was changed after the card was attached (%s). Your value was kept." % cur_hp)
    cur_args, orig_args = cfg.get("args", ""), record.get("args") or ""
    if cur_args == orig_args:
        pass
    elif after is None or cur_args == after.get("args"):
        actions.append({"op": "set", "key": "args", "value": orig_args} if orig_args
                       else {"op": "delete", "key": "args"})
    else:
        stripped = cur_args
        for arg in after.get("added_args") or []:
            stripped = re.sub(r"\s*(?<!\S)" + re.escape(arg) + r"(?!\S)", "", stripped).strip()
        for new, old in (after.get("replaced_args") or {}).items():
            if new in stripped:
                stripped = stripped.replace(new, old, 1)
        if stripped != cur_args:
            actions.append({"op": "set", "key": "args", "value": stripped} if stripped
                           else {"op": "delete", "key": "args"})
            notes.append("args were edited after the card was attached. Only the arguments this tool added "
                         "were removed; your edits were kept.")
        elif not any(_global_re(a).search(stripped) for a in after.get("added_args") or []):
            notes.append("args were edited after the card was attached. None of the arguments this tool "
                         "added are left in them, so your edits were kept as they are.")
        for arg in after.get("added_args") or []:
            now = _global_re(arg).search(stripped)
            if now:
                notes.append("%s was changed after the card was attached, so it was left as you set it." % now.group(0))
    if record.get("vga_changed"):
        cur_vga, orig_vga = cfg.get("vga"), record.get("vga")
        if cur_vga == orig_vga:
            pass
        elif after is None or cur_vga == after.get("vga"):
            actions.append({"op": "set", "key": "vga", "value": orig_vga} if orig_vga
                           else {"op": "delete", "key": "vga"})
        else:
            notes.append("The display setting (vga) was changed after the card was attached (%s). Your value "
                         "was kept." % (cur_vga or "unset"))
    return {"actions": actions, "notes": notes}


def cfg_holds_card(cfg, bdf, mappings):
    """True when any hostpci entry of the VM config passes *bdf* through, directly or by resource mapping."""
    want = norm_bdf(bdf)
    for key, value in cfg.items():
        if not re.fullmatch(r"hostpci\d+", key):
            continue
        for tok in re.split(r"[,;]", value):
            tok = tok.strip()
            tok = tok[5:] if tok.startswith("host=") else tok
            if tok.startswith("mapping="):
                if want in mappings.get(tok[len("mapping="):], ()):
                    return True
            elif norm_bdf(tok) == want:
                return True
    return False


def record_write(payload):
    """Write the attach record unless one for this very VM exists (it holds the true originals).
    A record left behind by a deleted VM whose id was reused is replaced."""
    path = Path(payload["path"])
    cfg = parse_config(payload["cfg_raw"])
    record = make_record(payload["vmid"], payload["bdf"], payload["vendor"], cfg, payload["hostpci"],
                         payload.get("global_args") or [], bool(payload.get("vga_none")), payload.get("oc", ""))
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = None
    if isinstance(old, dict) and old.get("fp", "") == record["fp"]:
        return {"written": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True))
    return {"written": True}


def load_mappings(root="/", run=default_run):
    """Resource mappings: {name: {card address, ...}} from /etc/pve/mapping/pci.cfg, else pvesh."""
    out = {}

    def add(name, path):
        for one in (path or "").split(";"):
            bdf = norm_bdf(one)
            if name and bdf:
                out.setdefault(name, set()).add(bdf)

    text = _read(_p(root, "/etc/pve/mapping/pci.cfg"))
    if text is not None:
        name = ""
        for line in text.splitlines():
            if line and not line[0].isspace() and not line.startswith("#"):
                name = line.strip().rstrip(":")
            elif line.strip().startswith("map "):
                for part in line.split(None, 1)[1].split(","):
                    key, _, value = part.strip().partition("=")
                    if key == "path":
                        add(name, value)
        return out
    code, raw = run(["pvesh", "get", "/cluster/mapping/pci", "--output-format", "json"])
    if code != 0:
        return out
    try:
        entries = json.loads(raw)
    except ValueError:
        return out
    for entry in entries if isinstance(entries, list) else []:
        for item in entry.get("map", []) if isinstance(entry, dict) else []:
            for part in (item.split(",") if isinstance(item, str) else []):
                key, _, value = part.partition("=")
                if key == "path":
                    add(entry.get("id"), value)
    return out


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
        _emit(build_target(root, payload["bdf"], payload["vendor"], payload.get("reset", "none"),
                           payload.get("identity")))
    elif cmd == "identity":
        _emit(identity_of(root, payload["bdf"]) or {})
    elif cmd == "mappings":
        _emit({k: sorted(v) for k, v in load_mappings(root).items()})
    elif cmd == "holds":
        print("yes" if cfg_holds_card(parse_config(payload["cfg_raw"]), payload["bdf"], load_mappings(root)) else "no")
    elif cmd == "lxc":
        for ctid, node in lxc_gpu_users(root):
            print("%d %s" % (ctid, node))
    elif cmd == "record-write":
        _emit(record_write(payload))
    elif cmd == "vm-restore":
        _emit(vm_restore(payload["record"], parse_config(payload["cfg_raw"])))
    elif cmd == "vm-stale":
        print("1" if record_vm_changed(payload["record"], parse_config(payload["cfg_raw"])) else "0")
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
            _emit({"ok": True, "restored": [], "errors": [], "kept": [], "reboot_required": False})
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
        sys.stderr.write("usage: host_setup.py facts|target|identity|mappings|holds|lxc|record-write|vm-restore|vm-stale|step|plan|preview|apply|undo|verify|state|state-save|reboot-pending|rebooted|mark-reboot|hint-set|hint-clear\n")
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
GPU_EMBED_HOST_SETUP_EOF
# ── GPU embedded: host_setup.py end ──
# ── GPU embedded: bios_guide.py begin ──
IFS= read -r -d '' GPU_EMBED_BIOS_GUIDE <<'GPU_EMBED_BIOS_GUIDE_EOF' || true
"""BIOS step of the GPU walkthrough: what is missing, and a card to photograph.

Standard library only (segno is optional, for QR codes) and no imports from this
package: the bash installer embeds this file verbatim (tests/test_gpu_parity.py
diffs the two) together with data/gpu/bios_paths.json, and runs it with
``python3 - <command>``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path

CARD_FILE = "/root/osx-proxmox-next-bios-steps.txt"
WIDTH = 76
OS_INDICATIONS = "/sys/firmware/efi/efivars/OsIndicationsSupported-8be4df61-93ca-11d2-aa0d-00e098032b8c"
# Longest names first: ASRock Rack before ASRock, PowerEdge before Dell, and so on.
_ORDER = ("asrock-rack", "asrock", "asus", "msi", "gigabyte", "supermicro", "dell-poweredge", "dell", "hpe",
          "hp", "lenovo-thinksystem", "lenovo", "intel-nuc", "mini-pc")


def _p(root, path):
    return Path(root) / path.lstrip("/")


def _read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


def load_data(path=None):
    path = path or os.environ.get("BIOS_DATA") or str(Path(__file__).with_name("data") / "gpu" / "bios_paths.json")
    return json.loads(Path(path).read_text())


def match_vendor(facts, data):
    """Key of the BIOS vendor in *data*, or 'generic' when nothing matches."""
    hay = " ".join(str(facts.get(k, "")) for k in ("board_vendor", "sys_vendor", "bios_vendor", "product_name",
                                                    "board_name")).lower()
    for key in _ORDER:
        for token in data["vendors"].get(key, {}).get("match", []):
            if re.search(r"\b" + re.escape(token), hay):
                return key
    return "generic"


def doc_url(data, key):
    slug = data["vendors"][key]["doc"] if key in data["vendors"] else "generic"
    return data["docs_base"] + slug


# ── what is missing ──────────────────────────────────────────────────

def above4g_off(facts, bdf):
    """True when the kernel could not place one of the card's BARs ("no space for" in dmesg).

    A card with a 256 MiB window works without Above 4G Decoding, so only this kernel message counts."""
    return any(b.startswith(bdf + ".") for b in facts.get("bar_errors", []))


def _driver(root, bdf):
    link = _p(root, "/sys/bus/pci/devices/%s.0/driver" % bdf)
    return link.resolve().name if link.exists() else None


def rebar_capable(root, bdf):
    base = _p(root, "/sys/bus/pci/devices/%s.0" % bdf)
    return any((base / ("resource%d_resize" % i)).exists() for i in range(6))


def check_items(facts, target, root="/"):
    """One entry per BIOS item: key, label, ok, detail. Only failing ones need the card."""
    intel = facts.get("cpu_vendor") == "intel"
    items = [
        {"key": "vtx" if intel else "svm", "ok": bool(facts.get("virtualization")),
         "detail": "The CPU does not report virtualization (vmx or svm)."},
        {"key": "vtd" if intel else "amd_iommu", "ok": bool(facts.get("dmar") or facts.get("ivrs") or facts.get("iommu_groups")),
         "detail": "The firmware does not publish the IOMMU table (DMAR or IVRS)."},
        {"key": "csm", "ok": bool(facts.get("efi")),
         "detail": "This host did not boot in UEFI mode (no /sys/firmware/efi)."},
    ]
    if target:
        bdf = target["bdf"]
        items.append({"key": "above4g", "ok": not above4g_off(facts, bdf),
                      "detail": "The kernel could not find room for the card's memory window."})
        others = [d for d in facts.get("displays", []) if not d["bdf"].startswith(bdf + ".")]
        held = _driver(root, bdf) == "vfio-pci"  # already handed to VMs: the host is not using it as its screen
        items.append({"key": "primary_display", "ok": not (target.get("boot_vga") and others and not held),
                      "detail": "The firmware uses the chosen card as the boot display."})
    return items


# ── the card ─────────────────────────────────────────────────────────

def _entry(data, vendor, key):
    ven = data["vendors"].get(vendor, {})
    return ven.get("paths", {}).get(key)


def _generic_hint(data, key):
    group = {"vtx": "virtualization", "svm": "virtualization", "vtd": "iommu", "amd_iommu": "iommu"}.get(key, key)
    return data["generic"].get(group, {"names": "", "where": ""})


def _line(width, left, value):
    dots = width - len(left) - len(value) - 2
    if dots < 3:
        return [left, "   = " + value]
    return [left + " " + "." * dots + " " + value]


def _wrap(text, width, indent):
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [indent]


def board_title(facts):
    name = " ".join(x for x in (facts.get("board_vendor", ""), facts.get("board_name", "")) if x).strip()
    return ((name or "this server") + " (%s)" % ("Intel" if facts.get("cpu_vendor") == "intel" else "AMD")).upper()


_ORDER_STEPS = (("csm", "CSM off"), ("above4g", "Above 4G Decoding on"), ("rebar", "Resizable BAR on Auto"))
QR_GAP = 3


def order_note(failing_keys, optional_rebar=False):
    """The ordering tip, naming only the settings that are still off. Empty when order cannot matter."""
    keys = set(failing_keys) | ({"rebar"} if optional_rebar else set())
    parts = [label for key, label in _ORDER_STEPS if key in keys]
    return "Do these in this order: " + ", then ".join(parts) + "." if len(parts) > 1 else ""


def build_card(failing, facts, data, vendor, resume_cmd="osx-next-cli gpu-setup", optional_rebar=False, qr=None,
               max_width=None):
    """ASCII-safe photo card (plain text). *failing* are the entries from check_items with ok false.

    *qr* (lines of the guide's QR code) goes inside the frame, so one photo of the screen holds the steps
    and the code. It sits beside the steps when the frame fits in *max_width* columns (no limit when
    None), and under the steps when it does not."""
    qr = list(qr or [])
    qr_w = max((len(q) for q in qr), default=0)
    width = WIDTH if max_width is None else max(48, min(WIDTH, max_width))
    text_w = width - 4
    beside = bool(qr) and (max_width is None or text_w + QR_GAP + qr_w + 4 <= max_width)
    inner = text_w + (qr_w + QR_GAP if beside else 0)
    ven = data["vendors"].get(vendor)
    body = ["BIOS STEPS FOR THIS SERVER", board_title(facts), "",
            "Take a photo of this screen, then reboot into the BIOS.", ""]
    if ven:
        body += _wrap("Open the BIOS with: %s. %s" % (ven["enter"], ven.get("mode", "")), text_w, "")
        body.append("")
    start = len(body)
    n = 0
    for item in failing:
        n += 1
        entry = _entry(data, vendor, item["key"])
        label = data["settings"][item["key"]]["label"]
        value = entry["value"] if entry else "Enabled"
        body += _line(text_w, "%d. %s" % (n, label), value)
        if entry:
            lead = "Path: " if entry["verified"] else "Usually under: "
            body += _wrap(lead + entry["path"], text_w, "   ")
        else:
            hint = _generic_hint(data, item["key"])
            body += _wrap("Look for: " + hint["names"], text_w, "   ")
            body += _wrap(hint["where"], text_w, "   ")
        if item["key"] in ("vtx", "svm"):
            body += _wrap("Without it no VM can start.", text_w, "   ")
    if optional_rebar:
        n += 1
        body += _line(text_w, "%d. Resizable BAR (optional)" % n, "Auto")
        body += _wrap("Leave it on Auto. If the VM will not start, set it to Disabled.", text_w, "   ")
    n += 1
    body.append("%d. Save and exit (usually F10)" % n)
    body.append("")
    body += _wrap(data["search_tip"], text_w, "")
    note = order_note([i["key"] for i in failing], optional_rebar)
    if note:
        body += _wrap(note, text_w, "")
    body += ["", "Back in Proxmox, run:", "   " + resume_cmd, "", "Same steps with pictures on your phone:",
             "   " + re.sub(r"^https?://", "", doc_url(data, vendor))]
    if qr and not beside:
        body += [""] + qr
    elif qr:
        while len(body) < start + len(qr):
            body.append("")
        for i, q in enumerate(qr):
            body[start + i] = body[start + i].ljust(text_w) + " " * QR_GAP + q
    border = "+" + "=" * (inner + 2) + "+"
    rows = [border] + ["| " + ln.ljust(inner) + " |" for ln in body] + [border]
    return "\n".join(rows)


def save_card(text, root="/"):
    path = _p(root, CARD_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return CARD_FILE


# ── QR ───────────────────────────────────────────────────────────────

def qr_lines(url, ascii_only=False):
    """QR code as text lines (dark module = block). Empty list when segno is not installed."""
    try:
        import segno  # type: ignore
    except ImportError:
        return []
    rows = [list(r) for r in segno.make(url, error="m", micro=False).matrix_iter(border=2)]
    if ascii_only:
        return ["".join("##" if c else "  " for c in r) for r in rows]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    glyph = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    return ["".join(glyph[(top, bot)] for top, bot in zip(a, b)) for a, b in zip(rows[0::2], rows[1::2])]


# ── reboot into the firmware setup ───────────────────────────────────

def firmware_setup_supported(root="/"):
    """True when the firmware allows 'reboot into setup' (OsIndicationsSupported bit 0)."""
    try:
        raw = _p(root, OS_INDICATIONS).read_bytes()
    except OSError:
        return False
    return len(raw) >= 5 and bool(raw[4] & 1)


def parse_running_vms(qm_list):
    out = []
    for line in qm_list.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[2] == "running":
            out.append((int(parts[0]), parts[1]))
    return out


REBOOT_ARGV = ["systemctl", "reboot", "--firmware-setup"]


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    root = os.environ.get("GPU_ROOT", "/")
    data = load_data()
    payload = json.loads(sys.stdin.read() or "{}")
    if cmd == "vendor":
        print(match_vendor(payload["facts"], data))
    elif cmd == "check":
        print(json.dumps(check_items(payload["facts"], payload.get("target"), root)))
    elif cmd == "card":
        facts = payload["facts"]
        failing = [i for i in payload["items"] if not i["ok"]]
        if not failing:
            return 3
        vendor = match_vendor(facts, data)
        rebar = bool(payload.get("target")) and rebar_capable(root, payload["target"]["bdf"])
        text = build_card(failing, facts, data, vendor, payload.get("resume_cmd") or "osx-next-cli gpu-setup", rebar,
                          payload.get("qr"), payload.get("max_width"))
        if payload.get("save"):
            save_card(text, root)
        print(text)
    elif cmd == "url":
        print(doc_url(data, payload.get("vendor") or match_vendor(payload["facts"], data)))
    elif cmd == "firmware":
        print("yes" if firmware_setup_supported(root) else "no")
    elif cmd == "running":
        for vmid, name in parse_running_vms(payload.get("qm_list", "")):
            print("%d %s" % (vmid, name))
    else:
        sys.stderr.write("usage: bios_guide.py vendor|check|card|url|firmware|running\n")
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
GPU_EMBED_BIOS_GUIDE_EOF
# ── GPU embedded: bios_guide.py end ──
# ── GPU embedded: bios_paths.json begin ──
IFS= read -r -d '' GPU_EMBED_BIOS_PATHS <<'GPU_EMBED_BIOS_PATHS_EOF' || true
{
 "version": 1,
 "docs_base": "https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/gpu/bios/",
 "search_tip": "Cannot find a setting? Use the BIOS search (the footer of the screen shows the key, often F3, F7 or F9) and type the setting name.",
 "order_note": "Do the settings in this order: CSM off, then Above 4G Decoding, then Resizable BAR if you want it. Virtualization and IOMMU are independent.",
 "settings": {
  "vtx": {
   "label": "Intel virtualization (VT-x)",
   "why": "Lets the CPU run virtual machines."
  },
  "svm": {
   "label": "AMD virtualization (SVM)",
   "why": "Lets the CPU run virtual machines."
  },
  "vtd": {
   "label": "Intel VT-d",
   "why": "Lets the host hand a PCI card to one VM safely (the IOMMU)."
  },
  "amd_iommu": {
   "label": "AMD IOMMU",
   "why": "Lets the host hand a PCI card to one VM safely."
  },
  "above4g": {
   "label": "Above 4G Decoding",
   "why": "Lets the GPU's large memory window sit above 4 GB."
  },
  "csm": {
   "label": "CSM (legacy boot support)",
   "why": "Must be off so the machine boots in pure UEFI mode."
  },
  "primary_display": {
   "label": "Primary display",
   "why": "The motherboard's own graphics should show the boot screen, not the card you pass through."
  },
  "rebar": {
   "label": "Resizable BAR (optional)",
   "why": "Optional. If the VM will not start, turn it off."
  }
 },
 "generic": {
  "virtualization": {
   "names": "Intel Virtualization Technology, VT-x, VMX, SVM Mode, AMD-V, Secure Virtual Machine",
   "where": "Usually under Advanced > CPU Configuration, Tweaker > Advanced CPU Settings, Settings > Advanced > CPU Features, or Security > Virtualization."
  },
  "iommu": {
   "names": "VT-d, Intel VT for Directed I/O, AMD IOMMU, IOMMU Support, AMD-Vi",
   "where": "Usually under Advanced > Chipset, North Bridge, System Agent (SA) Configuration, AMD CBS > NBIO Common Options, Miscellaneous (Gigabyte) or Processor Options (HPE)."
  },
  "above4g": {
   "names": "Above 4G Decoding, Above 4GB MMIO, Memory Mapped I/O above 4GB, PCI 64-bit Resource Handling above 4G, Above 4G memory / Crypto Currency mining (MSI)",
   "where": "Usually under Advanced > PCI Subsystem Settings, PCIe/PCI/PnP Configuration, North Bridge or IO Ports (Gigabyte)."
  },
  "csm": {
   "names": "CSM Support, Launch CSM, Legacy Support, Boot mode UEFI/Legacy, Legacy Option ROMs",
   "where": "Usually under Boot."
  },
  "primary_display": {
   "names": "Primary Display, Initial Display Output, Initial Graphics Adapter, Primary Graphics Adapter, VGA Priority, Primary Video Device, Select Active Video",
   "where": "Set it to IGD, IGFX, Onboard or iGPU. Servers keep the BMC graphics as the console and need no change."
  },
  "rebar": {
   "names": "Re-Size BAR Support, Resizable BAR, Smart Access Memory, Clever Access Memory",
   "where": "Appears only after Above 4G Decoding is on and CSM is off."
  }
 },
 "vendors": {
  "asus": {
   "name": "ASUS",
   "doc": "asus",
   "enter": "Del or F2",
   "mode": "Press F7 to switch between EZ Mode and Advanced Mode.",
   "manual": "https://www.asus.com/support/",
   "match": [
    "asustek",
    "asus"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > CPU Configuration > Intel (VMX) Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 6.2 CPU Configuration (p.63-64)"
    },
    "svm": {
     "path": "Advanced > CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf section 6.3 CPU Configuration (p.37)"
    },
    "vtd": {
     "path": "Advanced > System Agent (SA) Configuration > VT-d",
     "value": "Enabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 6.3 System Agent (SA) Configuration (p.67)"
    },
    "amd_iommu": {
     "path": "Advanced > AMD CBS > IOMMU (if it is not listed there, look inside AMD CBS > NBIO Common Options)",
     "value": "Enabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf section 6.13 AMD CBS (p.47): IOMMU is listed directly in AMD CBS, options Disabled, Enabled, Auto"
    },
    "above4g": {
     "path": "Advanced > PCI Subsystem Settings > Above 4G Decoding",
     "value": "Enabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 6.11 PCI Subsystem Settings (p.75); https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf section 6.4 (p.38)"
    },
    "rebar": {
     "path": "Advanced > PCI Subsystem Settings > Re-Size BAR Support (appears only after Above 4G Decoding is Enabled)",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 6.11 (p.75-76): item appears only when Above 4G Decoding is Enabled, and the manual says CSM must be Disabled"
    },
    "csm": {
     "path": "Boot > CSM (Compatibility Support Module) > Launch CSM",
     "value": "Disabled",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 8 Boot menu, CSM (Compatibility Support Module) > Launch CSM; the manual says Launch CSM is forced to Disabled when integrated graphics are used"
    },
    "primary_display": {
     "path": "Advanced > System Agent (SA) Configuration > Graphics Configuration > Primary Display (AMD boards: Advanced > NB Configuration > Primary Video Device)",
     "value": "CPU Graphics (AMD: IGFX Video)",
     "verified": true,
     "source": "https://dlcdnets.asus.com/pub/ASUS/mb/LGA1700/ROG_STRIX_Z790-E_GAMING_WIFI/E23863_ROG_STRIX_Z790_Series_BIOS_Manual_Intel_14th_EM_V2_WEB.pdf section 6.3 Graphics Configuration > Primary Display, options Auto, CPU Graphics, PEG Slot, PCIE (p.67-68); https://dlcdnets.asus.com/pub/ASUS/mb/Socket%20AM5/ROG_STRIX_X670E-E_GAMING_WIFI/E20544_ROG_STRIX_X670E_Series_BIOS_manual_EM_WEB.pdf section 6.12 NB Configuration > Primary Video Device, options IGFX Video, PCIE Video (p.46)"
    }
   }
  },
  "msi": {
   "name": "MSI",
   "doc": "msi",
   "enter": "Del",
   "mode": "Press F7 to switch between EZ mode and Advanced mode. F11 opens the boot menu.",
   "manual": "https://www.msi.com/support",
   "match": [
    "micro-star",
    "msi"
   ],
   "paths": {
    "vtx": {
     "path": "OC > CPU Features > Intel Virtualization Tech",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf OC Menu > CPU Features (Intel Virtualization Tech); this is under the OC menu, not Settings"
    },
    "svm": {
     "path": "OC > Advanced CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf OC Menu > Advanced CPU Configuration > SVM Mode (Enables or disables the AMD SVM (Secure Virtual Machine) Mode)"
    },
    "vtd": {
     "path": "OC > CPU Features > Intel VT-D Tech",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf OC Menu > CPU Features > Intel VT-D Tech"
    },
    "amd_iommu": {
     "path": "OC > AMD CBS > IOMMU",
     "value": "Enabled",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf OC Menu > AMD CBS > IOMMU (Enables or disables the IOMMU for I/O virtualization); not under Settings > Advanced"
    },
    "above4g": {
     "path": "Settings > Advanced > PCIe/PCI Sub-system Settings > Above 4G memory/ Crypto Currency mining",
     "value": "Enabled",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf SETTINGS > Advanced > PCIe/PCI Sub-system Settings; https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf same item"
    },
    "rebar": {
     "path": "Settings > Advanced > PCIe/PCI Sub-system Settings > Re-Size BAR Support",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf SETTINGS > Advanced > PCIe/PCI Sub-system Settings; https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf same item"
    },
    "csm": {
     "path": "Settings > Advanced > BIOS CSM/UEFI Mode (some guides print it as BIOS UEFI/ CSM Mode)",
     "value": "UEFI",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf Advanced section, BIOS UEFI/ CSM Mode ([CSM] or [UEFI]); https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf Advanced section, BIOS CSM/UEFI Mode. Boot > Boot Mode Select follows it automatically"
    },
    "primary_display": {
     "path": "Settings > Advanced > Integrated Graphics Configuration > Initiate Graphic Adapter",
     "value": "IGD (the onboard graphics)",
     "verified": true,
     "source": "https://download-2.msi.com/archive/mnu_exe/mb/AMDAM5BIOS.pdf and https://download.msi.com/archive/mnu_exe/mb/Intel700BIOS.pdf: Integrated Graphics Configuration > Initiate Graphic Adapter, options [IGD] and [PEG]; submenu appears only on CPUs with integrated graphics"
    }
   }
  },
  "gigabyte": {
   "name": "Gigabyte / Aorus",
   "doc": "gigabyte",
   "enter": "Del",
   "mode": "Press F2 to switch between Easy Mode and Advanced Mode.",
   "manual": "https://www.gigabyte.com/Support",
   "match": [
    "gigabyte",
    "aorus"
   ],
   "paths": {
    "vtx": {
     "path": "Tweaker > Advanced CPU Settings > Intel (VMX) Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Tweaker > Advanced CPU Settings (Intel (VMX) Virtualization Technology)"
    },
    "svm": {
     "path": "Tweaker > Advanced CPU Settings > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf chapter 2-4 Tweaker > Advanced CPU Settings > SVM Mode (AM4 B550 manual; no AM5 BIOS guide was reachable)"
    },
    "vtd": {
     "path": "Settings > Miscellaneous > VT-d",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Settings > Miscellaneous > VT-d"
    },
    "amd_iommu": {
     "path": "Settings > Miscellaneous > IOMMU",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf chapter 2-5 Settings > Miscellaneous > IOMMU (default Auto). AM4 B550 manual; AM5 wording not confirmed. AMD CBS is a separate Settings submenu"
    },
    "above4g": {
     "path": "Settings > IO Ports > Above 4G Decoding",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Settings > IO Ports > Above 4G Decoding; https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf same"
    },
    "rebar": {
     "path": "Settings > IO Ports > Re-Size BAR Support",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Settings > IO Ports > Re-Size BAR Support (follows Above 4G MMIO BIOS assignment)"
    },
    "csm": {
     "path": "Boot > CSM Support",
     "value": "Disabled",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Boot > CSM Support (Disabled supports UEFI boot only); https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf same"
    },
    "primary_display": {
     "path": "Settings > IO Ports > Initial Display Output",
     "value": "IGFX (the onboard graphics)",
     "verified": true,
     "source": "https://download.gigabyte.com/FileList/Manual/mb_manual_Z790-AORUS-XTREME-X-ICE_BIOS_e.pdf Settings > IO Ports > Initial Display Output (IGFX or PCIe 1 Slot); https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-elite-ax_e.pdf same"
    }
   }
  },
  "asrock": {
   "name": "ASRock",
   "doc": "asrock",
   "enter": "F2 or Del",
   "mode": "Press F6 to switch between EZ Mode and Advanced Mode.",
   "manual": "https://www.asrock.com/support",
   "match": [
    "asrock"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > CPU Configuration > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.1 CPU Configuration (p.58-60)"
    },
    "svm": {
     "path": "Advanced > CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.1 CPU Configuration (p.41). The X870 guide (https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf) lists no SVM item, so on newer boards search the BIOS for SVM"
    },
    "vtd": {
     "path": "Advanced > Chipset Configuration > VT-d",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 Chipset Configuration (p.61)"
    },
    "amd_iommu": {
     "path": "Advanced > AMD CBS > NBIO Common Options > IOMMU",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.7 AMD CBS > NBIO Common Options > IOMMU; https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.8"
    },
    "above4g": {
     "path": "Advanced > Chipset Configuration > Above 4G Decoding (AMD boards: Advanced > PCI Configuration > Above 4G Decoding)",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 Chipset Configuration (p.61); https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 PCI Configuration (p.40)"
    },
    "rebar": {
     "path": "Advanced > Chipset Configuration > C.A.M (Clever Access Memory) (AMD boards: Advanced > PCI Configuration > Re-Size BAR Support)",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 (p.61) calls it C.A.M (Clever Access Memory); https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 PCI Configuration calls it Re-Size BAR Support"
    },
    "csm": {
     "path": "Boot > CSM (Compatibility Support Module) > CSM (AMD boards: Boot > CSM)",
     "value": "Disabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.8 Boot Screen (p.91); https://download.asrock.com/Manual/Software/AMD%20X870/Software_BIOS%20Setup%20Guide_English.pdf section 3.8 Boot Screen, CSM options [Disabled] [Enabled]"
    },
    "primary_display": {
     "path": "Advanced > Chipset Configuration > Primary Graphics Adapter (Intel boards). AMD boards: Advanced > Onboard Devices Configuration > Integrated Graphics Controller",
     "value": "Onboard",
     "verified": true,
     "source": "https://download.asrock.com/Manual/Software/Intel%20Z790/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.2 (p.61): Primary Graphics Adapter, options Auto, Onboard, PCIE1. https://download.asrock.com/Manual/Software/AMD%20X670/Software_BIOS%20Setup%20Guide_English.pdf section 3.4.3 lists Integrated Graphics Controller (enable/disable) on AMD boards"
    }
   }
  },
  "asrock-rack": {
   "name": "ASRock Rack",
   "doc": "asrock-rack",
   "enter": "F2 or Del",
   "mode": "Boards with a BMC can be used through the IPMI remote console.",
   "manual": "https://www.asrockrack.com/support/",
   "match": [
    "asrockrack",
    "asrock rack"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > CPU Configuration > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/W680D4U-2L2T.pdf (W680D4U-2L2T) section 3.4.1 CPU Configuration"
    },
    "svm": {
     "path": "Advanced > CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/ROMED8-2T.pdf (ROMED8-2T) section 3.3.1 CPU Configuration (p.49); https://download.asrock.com/Manual/B650D4U.pdf (B650D4U) section 3.3.1 (p.50)"
    },
    "vtd": {
     "path": "Advanced > Chipset Configuration > VT-d",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/W680D4U-2L2T.pdf section 3.4.2 Chipset Configuration (p.81)"
    },
    "amd_iommu": {
     "path": "Advanced > AMD CBS > NBIO Common Options > IOMMU",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/B650D4U.pdf section 3.3.11 AMD CBS > NBIO Common Options > IOMMU (p.63-70). ROMED8-2T has AMD CBS > NBIO Common Options in section 3.3.9 (p.62)"
    },
    "above4g": {
     "path": "Advanced > PCI Subsystem Settings > Above 4G Decoding (EPYC boards such as ROMED8-2T: Advanced > Chipset Configuration > Above 4G Decoding)",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/B650D4U.pdf section 3.3.10 PCI Subsystem Settings (p.62); https://download.asrock.com/Manual/ROMED8-2T.pdf section 3.3.2 Chipset Configuration (p.51). The W680D4U-2L2T manual has no Above 4G item"
    },
    "rebar": {
     "path": "Advanced > PCI Subsystem Settings > Re-Size BAR Support (W680D4U: Advanced > Chipset Configuration > C.A.M (Clever Access Memory))",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://download.asrock.com/Manual/B650D4U.pdf section 3.3.10 (p.62); https://download.asrock.com/Manual/W680D4U-2L2T.pdf section 3.4.2 C.A.M (Clever Access Memory)"
    },
    "csm": {
     "path": "Boot > CSM Parameters > CSM (ROMED8-2T). The B650D4U and W680D4U manuals show no CSM item",
     "value": "Disabled",
     "verified": true,
     "source": "https://download.asrock.com/Manual/ROMED8-2T.pdf section 3.6.1 CSM Parameters (p.77). No CSM option appears in https://download.asrock.com/Manual/B650D4U.pdf or https://download.asrock.com/Manual/W680D4U-2L2T.pdf"
    },
    "primary_display": {
     "path": "Advanced > Chipset Configuration > Primary Display (B650D4U). Server boards keep the BMC graphics as console",
     "value": "Onboard",
     "verified": true,
     "source": "https://download.asrock.com/Manual/B650D4U.pdf section 3.3.2 Chipset Configuration > Primary Display (p.51)"
    }
   }
  },
  "supermicro": {
   "name": "Supermicro",
   "doc": "supermicro",
   "enter": "Del",
   "mode": "Boot menu is F11. The IPMI remote console works for all of this.",
   "manual": "https://www.supermicro.com/en/support/resources/downloadcenter/manual",
   "match": [
    "supermicro",
    "super micro"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > CPU Configuration > Intel (VMX) Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf (X12STL-F) section 4.3 Advanced > CPU Configuration (p.74)"
    },
    "svm": {
     "path": "Advanced > CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf (H13SSW) section 4.3 Advanced > CPU Configuration > SVM Mode"
    },
    "vtd": {
     "path": "Advanced > Chipset > System Agent (SA) Configuration > VT-d (Xeon Scalable boards may use Chipset Configuration > North Bridge > IIO Configuration > Intel VT for Directed I/O (VT-d))",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf section 4.3 Chipset > System Agent (SA) Configuration > VT-d (p.75). The Xeon Scalable (IIO Configuration) variant was not re-checked"
    },
    "amd_iommu": {
     "path": "Advanced > NB Configuration > IOMMU",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf section 4.3 NB Configuration > North Bridge Configuration > IOMMU, options Disabled, Enabled, Auto (p.68)"
    },
    "above4g": {
     "path": "Advanced > PCIe/PCI/PnP Configuration > Above 4G Decoding (Intel Xeon E boards: Above 4GB MMIO BIOS Assignment)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf section 4.3 PCIe/PCI/PnP Configuration > Above 4G Decoding; https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf same menu, item named Above 4GB MMIO BIOS Assignment"
    },
    "rebar": {
     "path": "Advanced > PCIe/PCI/PnP Configuration > Re-Size BAR Support (not present on the X12STL-F)",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf section 4.3 PCIe/PCI/PnP Configuration > Re-Size BAR Support"
    },
    "csm": {
     "path": "Security > Secure Boot > CSM Support. Also Boot > Boot Mode Select (Legacy, UEFI, Dual)",
     "value": "Disabled / UEFI",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf section 4.6 Security > CSM Support and section 4.7 Boot > Boot Mode Select; https://www.supermicro.com/manuals/motherboard/X12/MNL-2353.pdf section 4.6 and 4.7"
    },
    "primary_display": {
     "path": "Advanced > PCIe/PCI/PnP Configuration > VGA Priority",
     "value": "Onboard",
     "verified": true,
     "source": "https://www.supermicro.com/manuals/motherboard/H13/MNL-2564.pdf section 4.3, VGA Priority, options Onboard and External"
    }
   }
  },
  "dell": {
   "name": "Dell OptiPlex / Precision",
   "doc": "dell",
   "enter": "F2 at the Dell logo",
   "mode": "F12 opens the boot menu.",
   "manual": "https://www.dell.com/support",
   "match": [
    "dell"
   ],
   "paths": {
    "vtx": {
     "path": "Virtualization Support > Intel Virtualization Technology (older manuals call it Virtualization)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options Virtualization Support > Intel Virtualization Technology; https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support Virtualization support > Virtualization; https://www.dell.com/support/kbdoc/en-us/000195978/how-to-enable-or-disable-hardware-virtualization-on-dell-systems (Advanced > Virtualization or Virtualization Support > Virtualization; on 2025 and later systems Ctrl+Alt+S unhides it)"
    },
    "svm": {
     "path": "Virtualization Support > SVM Mode",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified for Dell AMD desktops: the official pages found cover Intel OptiPlex only. Previous wording kept from memory."
    },
    "vtd": {
     "path": "Virtualization Support > VT for Direct I/O",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options and https://www.dell.com/support/manuals/en-us/optiplex-7070-micro/opti7070_micro_setup_specs/virtualization-support: VT for Direct I/O (Enable Intel VT for Direct I/O)"
    },
    "amd_iommu": {
     "path": "Virtualization Support > IOMMU",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: no official Dell desktop page for an IOMMU switch was found. Previous wording kept from memory."
    },
    "above4g": {
     "path": "Settings > System Configuration > Above 4G Decoding (many Dell desktops have no such switch)",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: the OptiPlex 7020 owner's manual lists no Above 4G or MMIO item, so most Dell desktops likely have none. Previous wording kept from memory."
    },
    "rebar": {
     "path": "Performance > PCIe Resizable Base Address Register (BAR)",
     "value": "Disabled if the VM will not start",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options Performance > PCIe Resizable Base Address Register (BAR), off by default"
    },
    "csm": {
     "path": "Boot Configuration > Boot Sequence > Boot Mode: UEFI only (older BIOS: Enable Legacy Option ROMs off)",
     "value": "UEFI only, legacy option ROMs off",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options Boot Configuration > Boot Sequence lists only UEFI, and the Secure Boot note says Enable Legacy Option ROMs must be off"
    },
    "primary_display": {
     "path": "Display > Primary Display",
     "value": "Auto (or Intel HD Graphics)",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gb/optiplex-7020t-plus-desktop/optiplex-tower-plus-7020-owners-manual/system-setup-options Display > Primary Display, default Auto"
    }
   }
  },
  "dell-poweredge": {
   "name": "Dell PowerEdge",
   "doc": "dell",
   "enter": "F2 (System Setup)",
   "mode": "iDRAC: Next Boot > BIOS Setup.",
   "manual": "https://www.dell.com/support",
   "match": [
    "poweredge"
   ],
   "paths": {
    "vtx": {
     "path": "System Setup Main Menu > System BIOS > Processor Settings > Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings (R450) Processor Settings; the option also passes virtualization to the OS through the DMAR table"
    },
    "svm": {
     "path": "System Setup Main Menu > System BIOS > Processor Settings > Virtualization Technology (AMD servers have no separate SVM item)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings (R7515) Processor Settings: Virtualization Technology, no SVM option listed"
    },
    "vtd": {
     "path": "System BIOS > Processor Settings > Virtualization Technology (Intel servers have no separate VT-d item in the guide)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-us/poweredge-r450/per450_bios_ism_pub/processor-settings (R450) Processor Settings: only Virtualization Technology and Kernel DMA Protection are listed"
    },
    "amd_iommu": {
     "path": "System BIOS > Processor Settings > IOMMU Support",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-uk/poweredge-r7515/r7515_bios_pub/processor-settings (R7515) Processor Settings > IOMMU Support (default Enabled)"
    },
    "above4g": {
     "path": "System BIOS > Integrated Devices > Memory Mapped I/O above 4 GB",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-gh/poweredge-r750xa/per750xa_bios_pub_ism/integrated-devices (R750xa) Integrated Devices > Memory Mapped I/O above 4 GB (default Enabled)"
    },
    "csm": {
     "path": "System BIOS > Boot Settings > Boot Mode",
     "value": "UEFI",
     "verified": true,
     "source": "https://www.dell.com/support/manuals/en-us/poweredge-r740/r740_bios_ism_pub/boot-settings (R740) Boot Settings > Choosing system boot mode (BIOS or UEFI)"
    }
   }
  },
  "hp": {
   "name": "HP workstations and desktops",
   "doc": "hp",
   "enter": "F10 (press repeatedly as soon as the power light turns white)",
   "mode": "F9 opens the boot menu.",
   "manual": "https://support.hp.com",
   "match": [
    "hp",
    "hewlett-packard"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > System Options > Virtualization Technology (VTx)",
     "value": "Enabled",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf Table 6-3 Computer Setup Advanced > System Options; also https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf and https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf. Default is disabled; a full power off and on is needed after a change"
    },
    "svm": {
     "path": "Advanced > System Options > SVM CPU Virtualization",
     "value": "Enabled",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf Table 6-3 System Options: SVM CPU Virtualization (AMD products only), enables AMD-V and AMD-Vi"
    },
    "vtd": {
     "path": "Advanced > System Options > Virtualization Technology for Directed I/O (VTd)",
     "value": "Enabled",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf Table 6-3 System Options (Intel only, default disabled); https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf same"
    },
    "amd_iommu": {
     "path": "Advanced > System Options > SVM CPU Virtualization (it enables AMD-V and AMD-Vi, there is no separate IOMMU switch)",
     "value": "Enabled",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf Table 6-3 System Options: SVM CPU Virtualization 'Enables AMD-V and AMD-Vi'"
    },
    "above4g": {
     "path": "Advanced > Device Options > Above 4G MMIO (name varies)",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: the Z2 G9, Z4 G5 and Elite 800 G9 guides list no Above 4G or MMIO item. Previous wording kept from memory."
    },
    "csm": {
     "path": "Advanced > Boot Options (the current guides show UEFI Boot Order and no Legacy Support or CSM item)",
     "value": "UEFI",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_5451169_en-US-1.pdf, https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf, https://kaas.hpcloud.hp.com/pdf-public/pdf_7905584_en-US-1.pdf: Table 6-3 Boot Options lists UEFI Boot Order, After Boot Device Not Found and others; no Legacy Support item"
    },
    "primary_display": {
     "path": "Advanced > Built-In Device Options > VGA Boot Device (models with discrete graphics)",
     "value": "Integrated graphics",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf Table 6-3 Built-In Device Options > VGA Boot Device"
    },
    "rebar": {
     "path": "Advanced > Slot Settings > Resizable BARs",
     "value": "Enabled",
     "verified": true,
     "source": "https://kaas.hpcloud.hp.com/pdf-public/pdf_7582484_en-US-1.pdf (Z4 G5) Table 6-3 Slot Settings > Resizable BARs, default disabled"
    }
   }
  },
  "hpe": {
   "name": "HPE ProLiant",
   "doc": "hpe",
   "enter": "F9 (System Utilities)",
   "mode": "iLO remote console: Server > One-Button Boot > System Utilities.",
   "manual": "https://www.hpe.com/support",
   "match": [
    "hpe",
    "hewlett packard enterprise",
    "proliant"
   ],
   "paths": {
    "vtx": {
     "path": "System Configuration > BIOS/Platform Configuration (RBSU) > Virtualization Options > Intel(R) Virtualization Technology (Intel VT)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.hitachi.co.jp/products/it/ha8000v/download/manuals/hard/Gen11/UEFI/30-163527A4-001h.pdf (HPE UEFI System Utilities User Guide, Gen11, edition 10; PDF mirror on hitachi.co.jp) 'Enabling or disabling Virtualization Technology' (p.105)"
    },
    "svm": {
     "path": "System Configuration > BIOS/Platform Configuration (RBSU) > Virtualization Options > Virtualization Technology (the guide's note covers AMD-V under this same option)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.hitachi.co.jp/products/it/ha8000v/download/manuals/hard/Gen11/UEFI/30-163527A4-001h.pdf p.105 note: 'does not support AMD-V virtualization'; no separate SVM item is listed"
    },
    "vtd": {
     "path": "System Configuration > BIOS/Platform Configuration (RBSU) > Virtualization Options > Intel(R) VT-d",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.hitachi.co.jp/products/it/ha8000v/download/manuals/hard/Gen11/UEFI/30-163527A4-001h.pdf 'Enabling or disabling Intel VT-d' (p.106)"
    },
    "amd_iommu": {
     "path": "System Configuration > BIOS/Platform Configuration (RBSU) > Virtualization Options > AMD I/O Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.hitachi.co.jp/products/it/ha8000v/download/manuals/hard/Gen11/UEFI/30-163527A4-001h.pdf 'Enabling AMD I/O Virtualization Technology' (p.107)"
    },
    "above4g": {
     "path": "RBSU > Advanced Options > Memory Mapped I/O Above 4GB (also called PCI 64-bit Resource Handling above 4GB; the name differs per generation)",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: the Gen11 guide has no Memory Mapped I/O Above 4GB item. Previous wording kept from memory."
    },
    "csm": {
     "path": "RBSU > Boot Options > Boot Mode",
     "value": "UEFI Mode",
     "verified": false,
     "source": "Not verified: the guide mentions UEFI Mode and Legacy BIOS Mode but the exact menu path was not found. Previous wording kept from memory."
    }
   }
  },
  "lenovo": {
   "name": "Lenovo ThinkCentre / ThinkStation",
   "doc": "lenovo",
   "enter": "F1 (some models: Enter, then F1)",
   "mode": "F12 opens the boot menu.",
   "manual": "https://pubs.lenovo.com",
   "match": [
    "lenovo"
   ],
   "paths": {
    "vtx": {
     "path": "Security > Virtualization > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": false,
     "source": "Option names confirmed by Lenovo's ThinkStation PX guide (IOMMU, VT-d, Intel Virtualization Technology) but its menu path is only in screenshots. Previous path kept from memory."
    },
    "svm": {
     "path": "Security > Virtualization > AMD-V (SVM)",
     "value": "Enabled",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "vtd": {
     "path": "Security > Virtualization > Intel VT-d Feature",
     "value": "Enabled",
     "verified": false,
     "source": "Same as above: name confirmed, menu path only in screenshots. Previous path kept from memory."
    },
    "amd_iommu": {
     "path": "Advanced > AMD CBS > NBIO > IOMMU (ThinkStation P620)",
     "value": "Enabled",
     "verified": false,
     "source": "Inferred from the same vendor's other models, not checked"
    },
    "above4g": {
     "path": "Advanced > PCI Configuration > Above 4G Decoding (ThinkStation; ThinkCentre often has none)",
     "value": "Enabled",
     "verified": false,
     "source": "Inferred from the same vendor's other models, not checked"
    },
    "csm": {
     "path": "Startup > CSM Support and Startup > Boot Mode",
     "value": "Disabled / UEFI Only",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "primary_display": {
     "path": "Devices > Video Setup > Select Active Video",
     "value": "Integrated Graphics",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "rebar": {
     "path": "Devices > PCIe Setup > Re-size BAR Support (ThinkStation PX)",
     "value": "Enabled",
     "verified": true,
     "source": "https://download.lenovo.com/pccbbs/thinkcentre_pdf/workstationcomputesolutioninstallationguide.pdf section 2.2 Configure BIOS: 'Enable Re-size BAR Support under Devices -> PCIe Setup'"
    }
   }
  },
  "lenovo-thinksystem": {
   "name": "Lenovo ThinkSystem",
   "doc": "lenovo",
   "enter": "F1",
   "mode": "F12 opens the boot menu.",
   "manual": "https://pubs.lenovo.com",
   "match": [
    "thinksystem"
   ],
   "paths": {
    "vtx": {
     "path": "System Settings > Processors > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://pubs.lenovo.com/uefi_iot/processors (Lenovo UEFI reference, System Settings > Processors); the page lists only Intel Virtualization Technology there"
    },
    "svm": {
     "path": "System Settings > Processors > SVM Mode",
     "value": "Enabled",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "vtd": {
     "path": "System Settings > Processors > VT-d",
     "value": "Enabled",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "amd_iommu": {
     "path": "System Settings > Devices and I/O Ports > IOMMU",
     "value": "Enabled",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "above4g": {
     "path": "System Settings > Devices and I/O Ports > MM Config (Memory Mapped I/O above 4GB)",
     "value": "Enabled",
     "verified": false,
     "source": "Vendor manual wording from memory, not re-checked against a current manual"
    },
    "csm": {
     "path": "Boot Manager > Boot Modes > System Boot Mode (Lenovo docs: Boot manager > System > Boot Mode)",
     "value": "UEFI Mode",
     "verified": false,
     "source": "Partly confirmed: https://pubs.lenovo.com/uefi_iot/boot_manager says Boot manager > System > Boot Mode switches between UEFI and legacy mode, but the exact menu labels on a ThinkSystem were not confirmed."
    }
   }
  },
  "intel-nuc": {
   "name": "Intel NUC",
   "doc": "intel-nuc",
   "enter": "F2",
   "mode": "F10 opens the boot menu. Newer NUCs (13 and later) are made by ASUS and use the ASUS layout.",
   "manual": "https://www.intel.com/content/www/us/en/support/products/",
   "match": [
    "nuc"
   ],
   "paths": {
    "vtx": {
     "path": "Security > Security Features > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf (Intel NUC Aptio V BIOS Glossary rev 2.0, p.30): Aptio V BIOS Page Security > Security Features"
    },
    "vtd": {
     "path": "Security > Security Features > Intel VT for Directed I/O (VT-d)",
     "value": "Enabled",
     "verified": true,
     "source": "https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf p.30: Aptio V BIOS Page Security > Security Features (grayed out and forced on while Intel TXT is enabled)"
    },
    "above4g": {
     "path": "Usually not offered. NUC 12/13 Extreme: Advanced > Devices > PCI > Above 4G Decoding",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: the Aptio V glossary has no Above 4G item. Previous wording kept from memory."
    },
    "csm": {
     "path": "Boot > Boot Priority > UEFI Boot (enabled) and Legacy Boot (disabled)",
     "value": "UEFI Boot on, Legacy Boot off",
     "verified": true,
     "source": "https://www.intel.com/content/dam/support/us/en/documents/intel-nuc/NUC-AptioV-BIOS-Glossary.pdf Boot > Boot Priority: UEFI Boot and Legacy Boot checkboxes"
    }
   }
  },
  "mini-pc": {
   "name": "Minisforum / Beelink and other mini PCs",
   "doc": "mini-pc",
   "enter": "Del or F2",
   "mode": "F7 opens the boot menu on many models. Mini PCs often hide advanced menus.",
   "manual": "",
   "match": [
    "micro computer (hk)",
    "minisforum",
    "beelink",
    "azw",
    "shenzhen"
   ],
   "paths": {
    "vtx": {
     "path": "Advanced > CPU Configuration > Intel Virtualization Technology",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    },
    "svm": {
     "path": "Advanced > CPU Configuration > SVM Mode",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    },
    "vtd": {
     "path": "Advanced > System Agent (SA) Configuration > VT-d",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    },
    "amd_iommu": {
     "path": "Advanced > AMD CBS > NBIO Common Options > IOMMU (some models: Advanced > IOMMU)",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    },
    "above4g": {
     "path": "Advanced > PCI Subsystem Settings > Above 4G Decoding (often hidden)",
     "value": "Enabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    },
    "csm": {
     "path": "Boot > CSM Support",
     "value": "Disabled",
     "verified": false,
     "source": "Not verified: Minisforum and Beelink publish no BIOS menu documentation. Forum posts suggest Advanced > CPU Configuration > SVM Mode and Advanced > AMD CBS > NBIO Common Options > IOMMU on AMD models, but forums are not accepted sources."
    }
   }
  }
 }
}
GPU_EMBED_BIOS_PATHS_EOF
# ── GPU embedded: bios_paths.json end ──
# ── GPU embedded: amd_devices.txt begin ──
IFS= read -r -d '' GPU_EMBED_AMD_DEVICES <<'GPU_EMBED_AMD_DEVICES_EOF' || true
# AMD GPU allowlist for macOS guests (PCI vendor 1002).
# The Python package (amd_host.py) reads this file and the bash installer embeds it
# verbatim; tests/test_gpu_parity.py diffs the two. Change the file, then refresh the
# bash copy.
#
# Sources: Apple kext IOPCIMatch lists dumped on macOS 12.2.1 (github.com/cmsj/macOSrecon,
# data/GPU_AMD); Dortania GPU Buyers Guide (AMD page); OpenCore Legacy Patcher 3.0.0 AMD
# patchsets (these families are native on CPUs with AVX2, on macOS 13 to 26). The 13 to 26
# lists are assumed equal to the 12.2.1 dump (Apple only adds ids) and are not re-verified.
# Vendor-reset table: github.com/gnif/vendor-reset src/device-db.h.
#
# family|label|reset|boot_args|tahoe|ids
#   reset  : vendor-reset (card has the reset bug, install vendor-reset)
#            vendor-reset-warn (same, plus a hard warning: host storage corruption reports)
#            early-bind (not covered by vendor-reset, bind to vfio-pci before amdgpu loads)
#   tahoe  : ok (a Mac that Tahoe supports ships this family) or unverified (no Tahoe report)
#   ids    : hex device ids, ranges as A-B
polaris10|Polaris 10/20/30 (RX 470/480/570/580/590, WX 5100/7100)|vendor-reset||ok|67C0 67C1 67C2 67C4 67C7 67C8 67C9 67CA 67CC 67CF 67D0 67DF
polaris11|Polaris 11/21 (RX 460/560, WX 4100, Pro 560X)|vendor-reset||unverified|67E0 67E1 67E3 67E7 67E8 67E9 67EB 67EF 67FF
vega10|Vega 10 (RX Vega 56/64, Vega Frontier, WX 8200/9100)|vendor-reset||unverified|6860-686F 687F
vega20|Vega 20 (Radeon VII, Radeon Pro VII, Pro Vega II)|vendor-reset-warn||ok|66A0 66A1 66A2 66A3 66A7 66AF
navi10|Navi 10 (RX 5600 XT/5700/5700 XT, W5700)|vendor-reset|agdpmod=pikera|ok|7310 7312 7318 7319 731A 731B 731F
navi14|Navi 14 (RX 5300/5500 XT, W5500)|vendor-reset|agdpmod=pikera|ok|7340 7341 7343 7347 734F
navi21|Navi 21 (RX 6800/6800 XT/6900 XT, W6800)|early-bind|agdpmod=pikera|ok|73A2 73A3 73AB 73BF
navi23|Navi 23 (RX 6600/6600 XT, W6600)|early-bind|agdpmod=pikera|ok|73E3 73FF
# Cards that work when the device id is spoofed at the VM level (hostpci device-id option).
# spoof|real id|id the guest sees|family
spoof|73AF|73BF|navi21
spoof|73A5|73BF|navi21
spoof|73EF|73FF|navi23
# Never offered. deny|label|ids|one-line reason
deny|RX 6700 series (Navi 22)|73C0 73C1 73C3 73DF|RX 6700 series (Navi 22): Apple never shipped an accelerator for it.
deny|RX 6300/6400/6500 XT (Navi 24)|7420-743F|RX 6300/6400/6500 XT (Navi 24): macOS has no driver for it.
deny|RX 580 2048SP / RX 590 GME|6FDF|RX 580 2048SP and RX 590 GME: Apple's driver does not match this variant.
deny|RX 540/550 (Polaris 12)|6980-699F|RX 540/550 (Polaris 12): needs a device-id spoof and extra patches, not supported here.
deny|RX 7000 series (RDNA3)|7440-747F 7480-749F|RX 7000 series: Apple never shipped a driver.
deny|RX 9000 series (RDNA4)|7550-755F|RX 9000 series: Apple never shipped a driver.
GPU_EMBED_AMD_DEVICES_EOF
# ── GPU embedded: amd_devices.txt end ──
# ── GPU embedded: oc_boot_args.py begin ──
IFS= read -r -d '' GPU_EMBED_OC_BOOT_ARGS <<'GPU_EMBED_OC_BOOT_ARGS_EOF' || true
"""Add boot-args to (or restore) a VM's OpenCore config for a passed-through GPU.

Usage: oc_boot_args.py <EFI/OC directory> attach|detach|restore

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_gpu_parity.py diffs the two).
Standard library only.

attach  Backs up config.plist to config.plist.pre-gpu (kept if it already
        exists) and appends the boot-args named in the environment variable
        GPU_BOOT_ARGS (space separated; an arg that is already there is
        replaced, never duplicated). Refuses when config.plist itself still carries
        the NVIDIA (NullMoth) changes (AMFIPass kext entry, IONDRVSupport Block entry
        or the NullMoth boot-args), whatever backup files exist.
detach  Puts config.plist.pre-gpu back byte for byte and removes it, but only while
        config.plist still has the SHA-256 attach stored next to the backup. When the
        file changed since, the changed config and the backup stay and a KEPT line says
        so. With no backup it does nothing and succeeds, so an interrupted undo can be
        repeated. (restore is the same as detach.)
The config is always written to a temporary file and renamed into place.

Exit codes: 0 ok, 2 refused (nothing changed).
"""
import hashlib
import os
import plistlib
import shutil
import sys
import time

BACKUP = 'config.plist.pre-gpu'
PATCHED_SHA = BACKUP + '.patched-sha256'
NULLMOTH_ARGS = ('nvfb', 'nvaccel', 'nvfbheads', '-nvkmsnosmooth')
IONDRV = 'com.apple.iokit.IONDRVSupport'
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def merge_boot_args(current, wanted):
    keys = set(a.split('=', 1)[0] for a in wanted)
    kept = [t for t in current.split() if t.split('=', 1)[0] not in keys]
    return ' '.join(kept + list(wanted))


def file_sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_plist(cfg, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        plistlib.dump(cfg, f)
    # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
    with open(tmp, 'rb') as f:
        data = f.read()
    for tag in ('array', 'dict', 'data'):
        data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def nullmoth_leftovers(cfg):
    """What of the NVIDIA (NullMoth) setup config.plist still holds."""
    found = []
    if any(k.get('BundlePath') == 'AMFIPass.kext' for k in cfg.get('Kernel', {}).get('Add', [])):
        found.append('the AMFIPass kext entry')
    if any(b.get('Identifier') == IONDRV for b in cfg.get('Kernel', {}).get('Block', [])):
        found.append('the IONDRVSupport Block entry')
    args = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {}).get('boot-args', '')
    if any(t.split('=', 1)[0] in NULLMOTH_ARGS for t in args.split()):
        found.append('the NullMoth boot-args')
    return found


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    wanted = os.environ.get('GPU_BOOT_ARGS', '').split()
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    left = nullmoth_leftovers(cfg)
    if left:
        refuse('config.plist still has the NVIDIA (NullMoth) changes: ' + ', '.join(left) + '. '
               'Run nvidia-detach first. If that keeps your config, put back the original from the '
               'config.plist.pre-nullmoth.kept-* file on the OpenCore disk, or remove those entries by hand.')
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''), wanted)
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('GPU boot-args applied (backup: ' + BACKUP + ')\n')


def detach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    if not os.path.exists(backup):
        # already restored (an earlier undo got this far): nothing left to do
        sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
        return
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    if os.path.exists(stamp):
        with open(stamp) as f:
            wrote = f.read().strip()
        if os.path.exists(path) and file_sha(path) != wrote:
            kept = backup + '.kept-' + time.strftime('%Y%m%d%H%M%S')
            os.replace(backup, kept)
            os.remove(stamp)
            sys.stderr.write(
                'KEPT: config.plist was changed after the boot-args were added, so your version stays. '
                'The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
        os.remove(stamp)
    os.replace(backup, path)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    if len(argv) != 3 or argv[2] not in ('attach', 'detach', 'restore'):
        refuse('usage: oc_boot_args.py <EFI/OC dir> attach|detach|restore', 64)
    (attach if argv[2] == 'attach' else detach)(argv[1])


if __name__ == '__main__':
    main(sys.argv)
GPU_EMBED_OC_BOOT_ARGS_EOF
# ── GPU embedded: oc_boot_args.py end ──
# ── GPU embedded: intel_devices.txt begin ──
IFS= read -r -d '' GPU_EMBED_INTEL_DEVICES <<'GPU_EMBED_INTEL_DEVICES_EOF' || true
# Intel iGPU allowlist for macOS guests (PCI vendor 8086, WhateverXe).
# The Python package (igpu_engine.py) reads this file and the bash installer embeds it
# verbatim; tests/test_igpu_parity.py diffs the two. Change the file, then run
# scripts/embed_gpu_engines.py.
#
# Source: WhateverXe 0.8.0 kern_xelp_devices.hpp (github.com/b00t0x/WhateverXe, tag 0.8.0),
# whose ids were checked against Linux v6.12 i915_pciids.h.
#
# family|key|label|rom
#   rom: key of the pinned option ROM in igpu_engine.ROMS (LongQT-sea/intel-igpu-passthru v0.1)
# device|id|family|status|model
#   status: tested     passthrough proven end to end on real hardware
#           untested   in WhateverXe's table with an automatic backend, never run through this tool
#           nobackend  catalogued by WhateverXe but without an implemented backend: refused
family|tgl|Tiger Lake|tgl
family|adl-s|Alder Lake-S|tgl
family|adl-p|Alder Lake-P|adl-h
family|adl-n|Alder Lake-N|adl-n
family|rpl-s|Raptor Lake-S|tgl
family|rpl-p|Raptor Lake-P|adl-h
family|rkl|Rocket Lake|tgl
device|4680|adl-s|untested|Intel UHD Graphics 770
device|4682|adl-s|untested|Intel UHD Graphics 730
device|4688|adl-s|untested|Intel UHD Graphics
device|468A|adl-s|untested|Intel UHD Graphics
device|468B|adl-s|untested|Intel UHD Graphics
device|4690|adl-s|untested|Intel UHD Graphics 770
device|4692|adl-s|untested|Intel UHD Graphics 730
device|4693|adl-s|untested|Intel UHD Graphics 710
device|4626|adl-p|untested|Intel UHD Graphics
device|4628|adl-p|untested|Intel UHD Graphics
device|462A|adl-p|untested|Intel UHD Graphics
device|46A0|adl-p|untested|Intel UHD Graphics
device|46A1|adl-p|untested|Intel UHD Graphics
device|46A2|adl-p|untested|Intel UHD Graphics
device|46B0|adl-p|untested|Intel UHD Graphics
device|46B1|adl-p|untested|Intel UHD Graphics
device|46B2|adl-p|untested|Intel UHD Graphics
device|46C0|adl-p|untested|Intel UHD Graphics
device|46C1|adl-p|untested|Intel UHD Graphics
device|46C2|adl-p|untested|Intel UHD Graphics
device|46A3|adl-p|untested|Intel UHD Graphics
device|46A6|adl-p|tested|Intel Iris Xe Graphics
device|46A8|adl-p|untested|Intel Iris Xe Graphics
device|46AA|adl-p|untested|Intel Iris Xe Graphics
device|46B3|adl-p|untested|Intel UHD Graphics
device|46C3|adl-p|untested|Intel UHD Graphics
device|46D0|adl-n|untested|Intel UHD Graphics
device|46D1|adl-n|untested|Intel UHD Graphics
device|46D2|adl-n|untested|Intel UHD Graphics
device|46D3|adl-n|untested|Intel Graphics
device|46D4|adl-n|untested|Intel Graphics
device|4C8A|rkl|nobackend|Intel UHD Graphics 750
device|4C8B|rkl|nobackend|Intel UHD Graphics 730
device|9A40|tgl|untested|Intel Iris Xe Graphics
device|9A49|tgl|untested|Intel Iris Xe Graphics
device|9A59|tgl|untested|Intel UHD Graphics
device|9AC0|tgl|untested|Intel UHD Graphics
device|9AC9|tgl|untested|Intel UHD Graphics
device|9AD9|tgl|untested|Intel UHD Graphics
device|9AF8|tgl|untested|Intel UHD Graphics
device|9A60|tgl|untested|Intel UHD Graphics
device|9A68|tgl|untested|Intel UHD Graphics
device|9A70|tgl|untested|Intel UHD Graphics
device|9A78|tgl|untested|Intel UHD Graphics
device|A780|rpl-s|untested|Intel UHD Graphics 770
device|A781|rpl-s|untested|Intel UHD Graphics
device|A782|rpl-s|untested|Intel UHD Graphics 730
device|A783|rpl-s|untested|Intel UHD Graphics
device|A788|rpl-s|untested|Intel UHD Graphics
device|A789|rpl-s|untested|Intel UHD Graphics
device|A78A|rpl-s|untested|Intel UHD Graphics
device|A78B|rpl-s|untested|Intel UHD Graphics
device|A720|rpl-p|untested|Intel UHD Graphics
device|A721|rpl-p|untested|Intel UHD Graphics
device|A7A0|rpl-p|untested|Intel Iris Xe Graphics
device|A7A1|rpl-p|untested|Intel Iris Xe Graphics
device|A7A8|rpl-p|untested|Intel UHD Graphics
device|A7A9|rpl-p|untested|Intel UHD Graphics
device|A7AA|rpl-p|untested|Intel Graphics
device|A7AB|rpl-p|untested|Intel Graphics
device|A7AC|rpl-p|untested|Intel Graphics
device|A7AD|rpl-p|untested|Intel Graphics
GPU_EMBED_INTEL_DEVICES_EOF
# ── GPU embedded: intel_devices.txt end ──
# ── GPU embedded: igpu_engine.py begin ──
IFS= read -r -d '' GPU_EMBED_IGPU_ENGINE <<'GPU_EMBED_IGPU_ENGINE_EOF' || true
"""Intel iGPU passthrough engine: what the host reads and what the VM gets.

Standard library only and no imports from this package: the bash installer embeds this file
verbatim (tests/test_igpu_parity.py diffs the two) and runs it with ``python3``. The pure parts
(device list, option ROM pick, memory layout, VM settings) take plain values so they unit-test
without a host; the parts that touch the host take a ``root`` (default ``/``) and a ``run``
callable like host_setup.py does.

The recipe was proven live on an Alder Lake-P (8086:46a6) host on macOS 13, 14, 15 and 26:

* the host gives up the iGPU (i915 blacklisted, vfio-pci binds it at boot);
* the VM is i440fx (``machine: pc``) with the legacy IGD mode of vfio-pci, ``vga: none``;
* ``max-ram-below-4g`` is sized from the host's stolen-memory layout so the guest's graphics
  memory (the part WhateverXe reads) sits over the host's reservation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

VENDOR = 0x8086
IGD_SLOT = "00:02.0"
IGD_BDF = "0000:" + IGD_SLOT
KVM_DIR = "/usr/share/kvm"
MB = 1024 * 1024

# Pinned downloads. Every file is checked against its SHA-256 before use and never replaced
# silently: a mismatch is an error. Keep the URLs on tags or commits, never on a moving branch.
_LONGQT = "https://github.com/LongQT-sea/intel-igpu-passthru/releases/download/v0.1/"
_WEX = "https://github.com/b00t0x/WhateverXe/releases/download/0.8.0/"
_WEX_GFX = "https://github.com/b00t0x/WhateverXe-Graphics/"
_WEX_GFX_COMMIT = "88d64a340e6f0d52e3f32a136c920d190ac43c53"
ROMS = {
    "adl-h": ("ADL-H_RPL-H_GOPv21_igd.rom", _LONGQT + "ADL-H_RPL-H_GOPv21_igd.rom",
              "8316906f2a391c3c6830d6c311e7cb6436525d34ba28a861127ae1ce649c2b2d"),
    "tgl": ("RKL_TGL_ADL_RPL_GOPv17_igd.rom", _LONGQT + "RKL_TGL_ADL_RPL_GOPv17_igd.rom",
            "9d91bb6a5cc6d7c6dc4ab1783771b77972bf00c17e05d58850638eb389ff1ce4"),
    "adl-n": ("ADL-N_TWL_GOPv21_igd.rom", _LONGQT + "ADL-N_TWL_GOPv21_igd.rom",
              "2d2343fccbbeaf3d7df2280dd25a5f60a0aaf4b8e8ded15997b444d23afe0c3a"),
}
WEX_VERSION = "0.8.0"
PINS = {
    "whateverxe": ("WhateverXe-0.8.0-RELEASE.zip", _WEX + "WhateverXe-0.8.0-RELEASE.zip",
                   "5b71e14ce10c673f6baba276183f268d7f04766d298cd34f1df0bb96f11c8e4b"),
    "amfipass": ("AMFIPass-v1.4.1-Mod-RELEASE.zip",
                 _WEX_GFX + "raw/" + _WEX_GFX_COMMIT + "/extras/AMFIPass-v1.4.1-Mod-RELEASE.zip",
                 "71f41de42e355d2a83e6f093ef34a5b24d0a9c9a9085830b2317f1b874f56250"),
    "graphics": ("WhateverXe-Graphics-0.4.1.pkg", _WEX_GFX + "releases/download/v0.4.1/WhateverXe-Graphics-0.4.1.pkg",
                 "7f7ce3b924ad421cff0c4f30534598858474b8c9539c4b889a461bc1d5fed02c"),
}

# Memory layout. OVMF puts a constant 65 MB of its own allocations above the guest's graphics
# memory (measured on pve-edk2-firmware, PVE 9.2), WhateverXe compares the guest graphics memory
# top with the host reservation, and Apple's driver treats an opregion in 0xA0000000-0xBFFFFFFF
# as a pre-silicon emulator. Hence the margin, the constant and the two refused ranges.
OVMF_ALLOC_MB = 65
MARGIN_MB = 5
ALIGN_MB = 2
EMULATOR_LOW_MB, EMULATOR_HIGH_MB = 2560, 3072
PCI_HOLE_MB = 3584

PAYLOAD_LABEL = "OSXPNIGPU"
PAYLOAD_MB = 160
MAILBOX_MB = 1
LAUNCHD_PLIST = "com.osxproxmoxnext.igpu-firstboot.plist"
# data file name -> name on the payload disk
PAYLOAD_FILES = (("igpu_stage.sh", "stage.sh"), ("igpu_firstboot.sh", "firstboot.sh"),
                 ("igpu_launchd.plist", LAUNCHD_PLIST))
STAGE_COMMAND = "sh /Volumes/%s/stage.sh" % PAYLOAD_LABEL
BOOT_ARGS = ("-allow3d", "-disablegfxfirmware", "-wex-tgl-hold-render-wake")
GMS_ARG = "0x3"


def default_run(argv, timeout=20):
    """Run a command and return (exit code, stdout+stderr). (127, '') when it cannot start."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return res.returncode, (res.stdout or "") + (res.stderr or "")


# ── device list ──────────────────────────────────────────────────────

def data_dir():
    here = Path(__file__).resolve().parent
    return here / "data" / "gpu" if (here / "data" / "gpu").is_dir() else here


def devices_text():
    path = os.environ.get("IGPU_DEVICES") or str(data_dir() / "intel_devices.txt")
    return Path(path).read_text()


def parse_devices(text):
    """({family key: (label, rom key)}, {device id: (family key, status, model)}) from intel_devices.txt."""
    families, devices = {}, {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("|")
        if parts[0] == "family":
            families[parts[1]] = (parts[2], parts[3])
        else:
            devices[int(parts[1], 16)] = (parts[2], parts[3], parts[4])
    return families, devices


def lookup(device_id, text=None):
    """Everything known about a device id, or None when WhateverXe does not list it."""
    families, devices = parse_devices(text if text is not None else devices_text())
    entry = devices.get(device_id)
    if entry is None:
        return None
    family, status, model = entry
    label, rom = families[family]
    return {"device": device_id, "family": family, "family_label": label, "status": status,
            "model": model, "rom": rom, "rom_file": ROMS[rom][0]}


def verdict(device_id, text=None):
    """(kind, headline, detail): kind is intel, intel-untested or unsupported."""
    info = lookup(device_id, text)
    if info is None:
        return ("unsupported", "Not supported by macOS",
                "WhateverXe has no driver support for this Intel graphics chip (it covers Tiger Lake and "
                "Alder, Raptor Lake; older chips use Apple's own drivers and are not offered here).")
    if info["status"] == "nobackend":
        return ("unsupported", "Not supported yet",
                "%s is listed by WhateverXe but has no working backend yet." % info["family_label"])
    if info["status"] == "tested":
        return ("intel", "Works with WhateverXe (beta) on macOS 13 to 15, Tahoe 26 experimental",
                "%s (%s). Tested end to end." % (info["model"], info["family_label"]))
    return ("intel-untested", "Should work with WhateverXe (beta), untested on this chip",
            "%s (%s). Only Alder Lake-P 8086:46a6 has been tested through this tool." % (info["model"],
                                                                                         info["family_label"]))


# ── memory layout ────────────────────────────────────────────────────

def decode_gms(ggc):
    """Bytes of graphics stolen memory from the GGC register (Gen12: GMS is bits 15:8)."""
    gms = (ggc >> 8) & 0xFF
    if gms <= 0x10:
        return gms * 32 * MB
    if 0xF0 <= gms <= 0xFE:
        return (gms - 0xF0 + 1) * 4 * MB
    raise ValueError("unknown GMS value 0x%02x in GGC 0x%04x" % (gms, ggc))


def decode_bdsm(raw):
    """Base of the host's stolen memory: the BDSM register without its lock bit and low bits."""
    return raw & 0xFFF00000


def host_top(bdsm_raw, ggc):
    return decode_bdsm(bdsm_raw) + decode_gms(ggc)


def max_ram_below_4g_mb(top):
    """max-ram-below-4g (MB) that makes the guest's graphics memory cover the host reservation:
    host top + margin + OVMF's own allocations, rounded up to 2 MB."""
    need = top + (MARGIN_MB + OVMF_ALLOC_MB) * MB
    unit = ALIGN_MB * MB
    return -(-need // unit) * ALIGN_MB


def layout(bdsm_raw, ggc):
    """The memory layout for a host, or the reason it cannot be used."""
    try:
        top = host_top(bdsm_raw, ggc)
    except ValueError as exc:
        return {"ok": False, "issue": str(exc)}
    if decode_bdsm(bdsm_raw) == 0:
        return {"ok": False, "issue": "The iGPU reports no stolen memory (BDSM is 0). Check that the iGPU is "
                                      "enabled in the BIOS."}
    mb = max_ram_below_4g_mb(top)
    out = {"ok": True, "host_top": top, "bdsm": decode_bdsm(bdsm_raw), "gms_bytes": decode_gms(ggc),
           "max_ram_below_4g_mb": mb, "issue": ""}
    if EMULATOR_LOW_MB < mb <= EMULATOR_HIGH_MB:
        out.update(ok=False, issue=(
            "This host's graphics memory ends at 0x%X, so the guest would need %d MB of low RAM. That puts "
            "the OpRegion between 2.5 and 3.0 GB, where Apple's driver mistakes the guest for a pre-silicon "
            "emulator. Hosts like this need a WhateverXe patch (b00t0x/WhateverXe issue 57)." % (top, mb)))
    elif mb > PCI_HOLE_MB:
        out.update(ok=False, issue=(
            "This host's graphics memory ends at 0x%X, so the guest would need %d MB of low RAM. That is "
            "above 3.5 GB, where the PCI hole starts." % (top, mb)))
    return out


# ── VM settings ──────────────────────────────────────────────────────

def vm_settings(mb, rom_file, bdf=IGD_BDF):
    """What the VM config gets: machine, display, the passthrough device and the QEMU args to add."""
    return {
        "machine": "pc",
        "vga": "none",
        "hostpci0": "%s,legacy-igd=1,romfile=%s" % (bdf, rom_file),
        "args_add": ["-machine max-ram-below-4g=%dM" % mb,
                     "-set device.hostpci0.x-igd-lpc=on",
                     "-set device.hostpci0.x-igd-gms=" + GMS_ARG],
    }


def _arg_re(arg):
    key = arg.split("=", 1)[0]
    return re.compile(r"\s+".join(re.escape(p) for p in key.split()) + r"=\S+")


def merge_args(args, wanted):
    """*args* with each wanted ``-flag key=value`` set to the wanted value, never duplicated."""
    out = args or ""
    for arg in wanted:
        rx = _arg_re(arg)
        out = rx.sub(lambda _m, a=arg: a, out, count=1) if rx.search(out) else "%s %s" % (out.rstrip(), arg)
    return out.strip()


def strip_args(args, wanted):
    out = args or ""
    for arg in wanted:
        out = re.sub(r"\s*" + _arg_re(arg).pattern, "", out)
    return out.strip()


def recovery_bus(cfg):
    """The slot that holds the recovery disk: sata1 on an i440fx iGPU VM, ide2 on a q35 VM, else None."""
    for slot in ("sata1", "ide2"):
        if slot in cfg:
            return slot
    return None


# ── host checks ──────────────────────────────────────────────────────

def _p(root, path):
    return Path(root) / path.lstrip("/")


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _hex(path):
    text = _read(path)
    try:
        return int(text, 16) if text is not None else None
    except ValueError:
        return None


def read_registers(run=default_run):
    """(BDSM, GGC) of the iGPU from PCI config space, or None when setpci cannot read them."""
    code, out = run(["setpci", "-s", IGD_SLOT, "C0.L", "50.W"])
    words = out.split()
    if code != 0 or len(words) < 2:
        return None
    try:
        return int(words[0], 16), int(words[1], 16)
    except ValueError:
        return None


def bound_driver(root="/"):
    link = _p(root, "/sys/bus/pci/devices/%s/driver" % IGD_BDF)
    return link.resolve().name if link.exists() else None


def check(root="/", run=default_run, text=None):
    """Every host-side readiness check for the iGPU: issues refuse, warnings inform."""
    report = {"ok": False, "issues": [], "warnings": [], "hints": [], "device": None, "info": None,
              "layout": None, "driver": None}
    dev = _p(root, "/sys/bus/pci/devices/%s" % IGD_BDF)
    if not dev.exists():
        report["issues"].append("There is no PCI device at %s. The Intel iGPU must sit at 00:02.0." % IGD_SLOT)
        return report
    vendor, cls = _hex(dev / "vendor"), _hex(dev / "class")
    if vendor != VENDOR:
        report["issues"].append("The device at %s is not an Intel device." % IGD_SLOT)
        return report
    if cls is None or cls >> 8 != 0x0300:
        report["issues"].append("The Intel device at %s is not a VGA display controller." % IGD_SLOT)
        return report
    device = _hex(dev / "device") or 0
    report["device"] = device
    info = lookup(device, text)
    report["info"] = info
    kind, _head, detail = verdict(device, text)
    if kind == "unsupported":
        report["issues"].append("Intel %04x: %s" % (device, detail))
        return report
    if kind == "intel-untested":
        report["warnings"].append(
            "%s (8086:%04x) has not been tested through this tool. Only Alder Lake-P 8086:46a6 has. It should "
            "work: WhateverXe lists it." % (info["family_label"], device))
    report["driver"] = bound_driver(root)
    if report["driver"] != "vfio-pci":
        report["issues"].append(
            "The host still uses the iGPU (driver: %s). Run gpu-setup, apply the host step and reboot so "
            "vfio-pci holds it." % (report["driver"] or "none"))
    regs = read_registers(run)
    if regs is None:
        report["issues"].append("Could not read the iGPU memory registers with setpci (is pciutils installed?).")
    else:
        report["layout"] = layout(*regs)
        if not report["layout"]["ok"]:
            report["issues"].append(report["layout"]["issue"])
    group = dev / "iommu_group"
    if not group.exists():
        report["issues"].append("IOMMU is not active for the iGPU (no iommu_group). Enable VT-d in the BIOS.")
    report["ok"] = not report["issues"]
    return report


# ── downloads ────────────────────────────────────────────────────────

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, sha256, dest):
    """Download *url* to *dest* unless a file with the pinned hash is already there. Raises on mismatch."""
    dest = Path(dest)
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:  # noqa: S310 - pinned https URL
        shutil.copyfileobj(resp, out)
    got = sha256_file(part)
    if got != sha256:
        part.unlink()
        raise ValueError("checksum mismatch for %s: expected %s, got %s" % (url, sha256, got))
    part.replace(dest)
    return dest


def rom_for_family(family, text=None):
    families, _devices = parse_devices(text if text is not None else devices_text())
    return ROMS[families[family][1]]


# ── payload disk ─────────────────────────────────────────────────────

def render_payload_file(name, pkg_sha256):
    """A payload script with the package hash filled in."""
    return (data_dir() / name).read_text().replace("@PKG_SHA256@", pkg_sha256)


def build_payload(pkg, out, run=default_run):
    """Build the FAT32 payload disk (graphics package, stage and first-boot scripts, LaunchDaemon).

    A plain FAT32 volume without a partition table, so macOS recovery mounts it as /Volumes/OSXPNIGPU.
    The last MAILBOX_MB of the image stay outside the file system. Returns (ok, message)."""
    pkg_sha = sha256_file(pkg)
    out = Path(out)
    size = PAYLOAD_MB * MB
    with open(out, "wb") as f:
        f.truncate(size)
    blocks = (size - MAILBOX_MB * MB) // 1024
    code, msg = run(["mkfs.vfat", "-F", "32", "-n", PAYLOAD_LABEL, str(out), str(blocks)], 120)
    if code != 0:
        return False, "mkfs.vfat failed: " + msg.strip()
    mnt = tempfile.mkdtemp(prefix="igpu-payload-")
    try:
        code, msg = run(["mount", "-o", "loop", str(out), mnt], 60)
        if code != 0:
            return False, "could not mount the payload image: " + msg.strip()
        try:
            shutil.copyfile(pkg, os.path.join(mnt, "WhateverXe-Graphics.pkg"))
            for src, name in PAYLOAD_FILES:
                (Path(mnt) / name).write_text(render_payload_file(src, pkg_sha))
        finally:
            run(["sync"], 120)
            run(["umount", mnt], 60)
    finally:
        shutil.rmtree(mnt, ignore_errors=True)
    return True, "payload disk built (%d MB)" % PAYLOAD_MB


def read_result(img, run=default_run):
    """The text the guest left in result.txt on the payload disk, or '' (the VM must be stopped)."""
    mnt = tempfile.mkdtemp(prefix="igpu-result-")
    try:
        code, _msg = run(["mount", "-o", "loop,ro", str(img), mnt], 60)
        if code != 0:
            return ""
        try:
            return _read(os.path.join(mnt, "result.txt")) or ""
        finally:
            run(["umount", mnt], 60)
    finally:
        shutil.rmtree(mnt, ignore_errors=True)


# ── command line (used by the bash installer) ────────────────────────

def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _usage():
    sys.stderr.write("usage: igpu_engine.py check|verdict <id>|lookup <id>|settings <mb> <rom>|merge-args|strip-args|"
                     "fetch <rom:KEY|pin:NAME> <dir>|payload <pkg> <img>|result <img>|stage-command|pins\n")
    return 64


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    root = os.environ.get("GPU_ROOT", "/")
    if cmd == "check":
        _emit(check(root))
        return 0
    if cmd == "verdict" and len(argv) == 3:
        _emit(dict(zip(("kind", "headline", "detail"), verdict(int(argv[2], 16)))))
        return 0
    if cmd == "lookup" and len(argv) == 3:
        _emit(lookup(int(argv[2], 16)) or {})
        return 0
    if cmd == "settings" and len(argv) == 4:
        _emit(vm_settings(int(argv[2]), argv[3]))
        return 0
    if cmd in ("merge-args", "strip-args"):
        data = json.loads(sys.stdin.read())
        fn = merge_args if cmd == "merge-args" else strip_args
        sys.stdout.write(fn(data["args"], data["wanted"]) + "\n")
        return 0
    if cmd == "fetch" and len(argv) == 4:
        kind, _, key = argv[2].partition(":")
        entry = ROMS.get(key) if kind == "rom" else PINS.get(key)
        if entry is None:
            sys.stderr.write("unknown download %s\n" % argv[2])
            return 64
        try:
            path = fetch(entry[1], entry[2], Path(argv[3]) / entry[0])
        except (OSError, ValueError) as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 1
        sys.stdout.write(str(path) + "\n")
        return 0
    if cmd == "payload" and len(argv) == 4:
        ok, msg = build_payload(argv[2], argv[3])
        sys.stderr.write(msg + "\n")
        return 0 if ok else 1
    if cmd == "result" and len(argv) == 3:
        sys.stdout.write(read_result(argv[2]) + "\n")
        return 0
    if cmd == "stage-command":
        sys.stdout.write(STAGE_COMMAND + "\n")
        return 0
    if cmd == "pins":
        _emit({"roms": {k: list(v) for k, v in ROMS.items()}, "pins": {k: list(v) for k, v in PINS.items()}})
        return 0
    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
GPU_EMBED_IGPU_ENGINE_EOF
# ── GPU embedded: igpu_engine.py end ──
# ── GPU embedded: oc_igpu.py begin ──
IFS= read -r -d '' GPU_EMBED_OC_IGPU <<'GPU_EMBED_OC_IGPU_EOF' || true
"""Set up (or undo) a VM's OpenCore disk for Intel iGPU passthrough with WhateverXe.

Usage: oc_igpu.py <EFI/OC directory> attach|restore|fadt|stage|unstage

The Python CLI/TUI and the standalone bash installer run this exact text (the
bash script embeds it verbatim; tests/test_igpu_parity.py diffs the two).
Standard library only.

attach   Backs up config.plist to config.plist.pre-igpu (kept if it already exists), unpacks
         WhateverXe.kext and AMFIPass.kext from the two zips named in the environment
         (IGPU_WEX_ZIP / IGPU_WEX_SHA256 and IGPU_AMFI_ZIP / IGPU_AMFI_SHA256, each checked
         against its pinned SHA-256 first) into Kexts/, lists both right after Lilu, turns
         WhateverGreen off, sets ACPI > Quirks > FadtEnableReset and adds the boot-args
         -allow3d -disablegfxfirmware -wex-tgl-hold-render-wake. Running it twice changes
         nothing the second time. Refuses when config.plist still carries the NVIDIA
         (NullMoth) changes: the IONDRVSupport Block entry or the nvfb/nvaccel boot-args.
fadt     Only sets ACPI > Quirks > FadtEnableReset, with no backup. The i440fx machine type the iGPU
         needs hangs every guest restart without it, so a new VM gets it before the install starts.
stage    Makes the picker show only the recovery disk and wait for a key: Timeout 0 and ScanPolicy 0x201 (HFS+
         only, so the installed APFS macOS, which the picker would otherwise highlight as the startup disk,
         is hidden). Used right before the VM boots recovery to stage the graphics package.
unstage  Puts ScanPolicy back (the value saved by stage, else 0). The caller restores the picker timeout.
restore  Puts config.plist.pre-igpu back byte for byte and removes the two kexts, but only
         while config.plist still has the SHA-256 attach stored next to the backup. When the
         file changed since, the changed config and the backup stay and a KEPT line says so.
         With no backup it does nothing and succeeds, so an interrupted undo can be repeated.
The config is always written to a temporary file and renamed into place.

Exit codes: 0 ok, 2 refused (nothing changed), 64 usage.
"""
import hashlib
import os
import plistlib
import shutil
import sys
import time
import zipfile

BACKUP = 'config.plist.pre-igpu'
PATCHED_SHA = BACKUP + '.patched-sha256'
KEXTS = (('WhateverXe.kext', 'WhateverXe', 'IGPU_WEX'), ('AMFIPass.kext', 'AMFIPass', 'IGPU_AMFI'))
BOOT_ARGS = ('-allow3d', '-disablegfxfirmware', '-wex-tgl-hold-render-wake')
NVRAM_GUID = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
NULLMOTH_ARGS = ('nvfb', 'nvaccel', 'nvfbheads', '-nvkmsnosmooth')
IONDRV = 'com.apple.iokit.IONDRVSupport'


def refuse(message, code=2):
    sys.stderr.write('ERROR: ' + message + '\n')
    sys.exit(code)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_plist(cfg, path):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        data = plistlib.dumps(cfg)
        # plistlib emits self-closing tags that OpenCore's OcXmlLib rejects
        for tag in ('array', 'dict', 'data'):
            data = data.replace(('<%s/>' % tag).encode(), ('<%s></%s>' % (tag, tag)).encode())
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def merge_boot_args(current, wanted):
    keys = set(a.split('=', 1)[0] for a in wanted)
    kept = [t for t in current.split() if t.split('=', 1)[0] not in keys]
    return ' '.join(kept + list(wanted))


def unpack_kext(zip_path, expected_sha, bundle, kexts_dir):
    """Unpack *bundle* from a verified zip into kexts_dir, replacing an older copy."""
    if not zip_path or not os.path.exists(zip_path):
        refuse('the %s zip is missing (%s)' % (bundle, zip_path or 'not set'))
    actual = file_sha(zip_path)
    if actual != expected_sha:
        refuse('%s zip checksum mismatch: expected %s, got %s' % (bundle, expected_sha, actual))
    dest = os.path.join(kexts_dir, bundle)
    tmp = dest + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or not name.startswith(bundle + '/'):
                continue
            if name.startswith('/') or '..' in name.split('/'):
                refuse('unsafe path in the %s zip: %s' % (bundle, name))
            out = os.path.join(tmp, name[len(bundle) + 1:])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    if count == 0:
        refuse('no %s inside the zip' % bundle)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def kext_entry(bundle, exe):
    return {'Arch': 'Any', 'BundlePath': bundle, 'Comment': 'iGPU passthrough', 'Enabled': True,
            'ExecutablePath': 'Contents/MacOS/' + exe, 'MaxKernel': '', 'MinKernel': '',
            'PlistPath': 'Contents/Info.plist'}


def nullmoth_leftovers(cfg):
    """What of the NVIDIA (NullMoth) setup config.plist still holds."""
    found = []
    if any(b.get('Identifier') == IONDRV for b in cfg.get('Kernel', {}).get('Block', [])):
        found.append('the IONDRVSupport Block entry')
    args = cfg.get('NVRAM', {}).get('Add', {}).get(NVRAM_GUID, {}).get('boot-args', '')
    if any(t.split('=', 1)[0] in NULLMOTH_ARGS for t in args.split()):
        found.append('the NullMoth boot-args')
    return found


def attach(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    adds = cfg.get('Kernel', {}).get('Add', [])
    left = nullmoth_leftovers(cfg)
    if left:
        refuse('config.plist still has the NVIDIA (NullMoth) changes: ' + ', '.join(left) + '. '
               'Run nvidia-detach first.')
    kexts_dir = os.path.join(oc_dir, 'Kexts')
    for bundle, _exe, prefix in KEXTS:
        unpack_kext(os.environ.get(prefix + '_ZIP', ''), os.environ.get(prefix + '_SHA256', ''), bundle, kexts_dir)
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    ours = [b for b, _e, _p in KEXTS]
    kexts = [k for k in adds if k.get('BundlePath') not in ours]
    pos = None
    for idx, k in enumerate(kexts):
        if k.get('BundlePath') == 'WhateverGreen.kext':
            k['Enabled'] = False
        if k.get('BundlePath') == 'Lilu.kext':
            pos = idx + 1
    if pos is None:
        refuse('config.plist has no Lilu.kext entry, so WhateverXe cannot be placed after it')
    for bundle, exe, _p in KEXTS:
        kexts.insert(pos, kext_entry(bundle, exe))
        pos += 1
    cfg['Kernel']['Add'] = kexts
    cfg.setdefault('ACPI', {}).setdefault('Quirks', {})['FadtEnableReset'] = True
    nv = cfg.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault(NVRAM_GUID, {})
    nv['boot-args'] = merge_boot_args(nv.get('boot-args', ''), BOOT_ARGS)
    write_plist(cfg, path)
    with open(os.path.join(oc_dir, PATCHED_SHA), 'w') as f:
        f.write(file_sha(path) + '\n')
    sys.stderr.write('iGPU OpenCore setup applied (backup: ' + BACKUP + ')\n')


def fadt(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    cfg.setdefault('ACPI', {}).setdefault('Quirks', {})['FadtEnableReset'] = True
    write_plist(cfg, path)
    sys.stderr.write('FadtEnableReset set\n')


STAGE_SCAN_POLICY = 0x201  # lock to file systems, allow HFS+ only
SCAN_SAVED = 'igpu-scanpolicy.orig'


def stage(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    sec = cfg.setdefault('Misc', {}).setdefault('Security', {})
    saved = os.path.join(oc_dir, SCAN_SAVED)
    if not os.path.exists(saved) and sec.get('ScanPolicy', 0) != STAGE_SCAN_POLICY:
        with open(saved, 'w') as f:
            f.write(str(sec.get('ScanPolicy', 0)) + '\n')
    sec['ScanPolicy'] = STAGE_SCAN_POLICY
    cfg['Misc'].setdefault('Boot', {})['Timeout'] = 0
    write_plist(cfg, path)
    sys.stderr.write('picker shows recovery only and waits for a key\n')


def unstage(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    with open(path, 'rb') as f:
        cfg = plistlib.load(f)
    saved = os.path.join(oc_dir, SCAN_SAVED)
    sec = cfg.setdefault('Misc', {}).setdefault('Security', {})
    if sec.get('ScanPolicy') == STAGE_SCAN_POLICY:
        original = 0
        if os.path.exists(saved):
            with open(saved) as f:
                original = int(f.read().strip() or 0)
        sec['ScanPolicy'] = original
        write_plist(cfg, path)
    if os.path.exists(saved):
        os.remove(saved)
    sys.stderr.write('ScanPolicy restored\n')


def restore(oc_dir):
    path = os.path.join(oc_dir, 'config.plist')
    backup = os.path.join(oc_dir, BACKUP)
    if not os.path.exists(backup):
        sys.stderr.write('no ' + BACKUP + ' found, nothing to restore\n')
        return
    stamp = os.path.join(oc_dir, PATCHED_SHA)
    if os.path.exists(stamp):
        with open(stamp) as f:
            wrote = f.read().strip()
        if os.path.exists(path) and file_sha(path) != wrote:
            kept = backup + '.kept-' + time.strftime('%Y%m%d%H%M%S')
            os.replace(backup, kept)
            os.remove(stamp)
            sys.stderr.write(
                'KEPT: config.plist was changed after the iGPU setup was added, so your version stays. '
                'The original is on the disk as ' + os.path.basename(kept) + '.\n')
            return
        os.remove(stamp)
    os.replace(backup, path)
    for bundle, _exe, _p in KEXTS:
        shutil.rmtree(os.path.join(oc_dir, 'Kexts', bundle), ignore_errors=True)
    sys.stderr.write('OpenCore config restored from ' + BACKUP + '\n')


def main(argv):
    actions = {'attach': attach, 'restore': restore, 'fadt': fadt, 'stage': stage, 'unstage': unstage}
    if len(argv) != 3 or argv[2] not in actions:
        refuse('usage: oc_igpu.py <EFI/OC dir> attach|restore|fadt|stage|unstage', 64)
    actions[argv[2]](argv[1])


if __name__ == '__main__':
    main(sys.argv)
GPU_EMBED_OC_IGPU_EOF
# ── GPU embedded: oc_igpu.py end ──
# ── GPU embedded: igpu_firstboot.sh begin ──
IFS= read -r -d '' GPU_EMBED_IGPU_FIRSTBOOT <<'GPU_EMBED_IGPU_FIRSTBOOT_EOF' || true
#!/bin/sh
# osx-proxmox-next: first boot of a macOS guest that has the Intel iGPU passed through.
# Installs the WhateverXe Graphics package, but only once WhateverXe is loaded: installing it
# without WhateverXe makes the guest panic at boot. Started by the LaunchDaemon
# com.osxproxmoxnext.igpu-firstboot (root). At most 3 tries; the guest stays usable without it.
BASE="/Library/Application Support/OSXProxmoxNext"
PKG="$BASE/WhateverXe-Graphics.pkg"
TRIES_FILE="$BASE/igpu-tries"
STATUS_FILE="$BASE/igpu-status"
PLIST="/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
LOG="/var/log/osx-proxmox-next-igpu.log"
WANT_SHA256="@PKG_SHA256@"
PAYLOAD_LABEL="OSXPNIGPU"
WAIT_SECONDS=120
MAX_TRIES=3

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

# status, then hand it to the host through the payload disk when that is attached
finish() {
  echo "$1" >"$STATUS_FILE"
  log "status: $1"
  vol="/Volumes/$PAYLOAD_LABEL"
  [ -d "$vol" ] || diskutil mount "$PAYLOAD_LABEL" >/dev/null 2>&1
  if [ -d "$vol" ]; then
    echo "$1" >"$vol/result.txt"
    sync
    diskutil unmount "$vol" >/dev/null 2>&1
    # The host waits for the guest to stop, then reads result.txt and starts it again.
    log "stopping so the host can restart the guest"
    /sbin/shutdown -h now >/dev/null 2>&1
  elif [ "$1" = "installed" ]; then
    log "no payload disk, restarting once to load the graphics drivers"
    /sbin/shutdown -r now >/dev/null 2>&1
  fi
  exit 0
}

tries=$(cat "$TRIES_FILE" 2>/dev/null || echo 0)
if [ "$tries" -ge "$MAX_TRIES" ]; then
  log "already tried $tries times, leaving the package alone"
  rm -f "$PLIST"
  exit 0
fi
echo $((tries + 1)) >"$TRIES_FILE"

if [ ! -f "$PKG" ]; then
  log "package is not there"
  rm -f "$PLIST"
  finish "failed: package missing"
fi

got=$(/usr/bin/shasum -a 256 "$PKG" | awk '{print $1}')
if [ "$got" != "$WANT_SHA256" ]; then
  log "package checksum mismatch: $got"
  rm -f "$PKG" "$PLIST"
  finish "failed: package checksum mismatch"
fi

waited=0
while [ "$waited" -lt "$WAIT_SECONDS" ]; do
  if /usr/bin/kmutil showloaded 2>/dev/null | grep -q org.b00t0x.WhateverXe; then
    break
  fi
  sleep 5
  waited=$((waited + 5))
done
if ! /usr/bin/kmutil showloaded 2>/dev/null | grep -q org.b00t0x.WhateverXe; then
  log "WhateverXe did not load within ${WAIT_SECONDS}s (try $((tries + 1)) of $MAX_TRIES)"
  finish "not installed: WhateverXe not loaded (try $((tries + 1)) of $MAX_TRIES)"
fi

log "WhateverXe is loaded, installing the graphics package"
if /usr/sbin/installer -pkg "$PKG" -target / >>"$LOG" 2>&1; then
  rm -f "$PKG" "$PLIST" "$BASE/firstboot.sh" "$TRIES_FILE"
  finish "installed"
fi
finish "failed: installer error (try $((tries + 1)) of $MAX_TRIES)"
GPU_EMBED_IGPU_FIRSTBOOT_EOF
# ── GPU embedded: igpu_firstboot.sh end ──
# ── GPU embedded: igpu_stage.sh begin ──
IFS= read -r -d '' GPU_EMBED_IGPU_STAGE <<'GPU_EMBED_IGPU_STAGE_EOF' || true
#!/bin/sh
# osx-proxmox-next: runs in the macOS recovery Terminal, from the payload disk (volume OSXPNIGPU).
# Copies the graphics package, the first-boot script and its LaunchDaemon into the installed
# macOS (offline), checks the package, writes result.txt next to this script and powers off.
SRC="$(cd "$(dirname "$0")" && pwd)"
say() { echo "osx-proxmox-next: $*"; }
fail() { say "FAILED: $*"; echo "failed: $*" >"$SRC/result.txt"; sync; /sbin/halt; exit 1; }

diskutil mount "MACOS - Data" >/dev/null 2>&1
D="/Volumes/MACOS - Data"
[ -d "$D/Library" ] || D="/Volumes/Data"
[ -d "$D/Library" ] || fail "the installed macOS data volume is not mounted"

BASE="$D/Library/Application Support/OSXProxmoxNext"
mkdir -p "$BASE" "$D/Library/LaunchDaemons" || fail "cannot create the target folders"
cp "$SRC/WhateverXe-Graphics.pkg" "$BASE/WhateverXe-Graphics.pkg" || fail "cannot copy the package"
cp "$SRC/firstboot.sh" "$BASE/firstboot.sh" || fail "cannot copy the first-boot script"
cp "$SRC/com.osxproxmoxnext.igpu-firstboot.plist" "$D/Library/LaunchDaemons/" || fail "cannot copy the LaunchDaemon"
chmod 755 "$BASE/firstboot.sh"
chmod 644 "$BASE/WhateverXe-Graphics.pkg" "$D/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
chown -R 0:0 "$BASE" "$D/Library/LaunchDaemons/com.osxproxmoxnext.igpu-firstboot.plist"
rm -f "$BASE/igpu-tries" "$BASE/igpu-status"

# The payload disk was built from the verified download. Compare the copy with it (byte for byte where cmp
# exists, by size otherwise); firstboot.sh checks the SHA-256 again inside the full macOS.
size_of() { ls -ln "$1" | awk '{print $5}'; }
if command -v cmp >/dev/null 2>&1; then
  cmp -s "$SRC/WhateverXe-Graphics.pkg" "$BASE/WhateverXe-Graphics.pkg" || \
    fail "the copied package differs from the original ($(size_of "$SRC/WhateverXe-Graphics.pkg") and $(size_of "$BASE/WhateverXe-Graphics.pkg") bytes)"
else
  [ "$(size_of "$SRC/WhateverXe-Graphics.pkg")" = "$(size_of "$BASE/WhateverXe-Graphics.pkg")" ] || fail "the copied package has the wrong size"
fi

say "staged OK"
echo "staged" >"$SRC/result.txt"
sync
/sbin/halt
GPU_EMBED_IGPU_STAGE_EOF
# ── GPU embedded: igpu_stage.sh end ──
# ── GPU embedded: igpu_launchd.plist begin ──
IFS= read -r -d '' GPU_EMBED_IGPU_PLIST <<'GPU_EMBED_IGPU_PLIST_EOF' || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.osxproxmoxnext.igpu-firstboot</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/sh</string>
		<string>/Library/Application Support/OSXProxmoxNext/firstboot.sh</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
</dict>
</plist>
GPU_EMBED_IGPU_PLIST_EOF
# ── GPU embedded: igpu_launchd.plist end ──

# Write the embedded engines to a temp dir once.
function gpu_engine_init() {
  [ -n "${GPU_ENGINE_DIR:-}" ] && [ -d "$GPU_ENGINE_DIR" ] && return 0
  GPU_ENGINE_DIR=$(mktemp -d "${TEMP_DIR:-${TMPDIR:-/tmp}}/gpuengine.XXXXXX") || return 1
  printf '%s' "$GPU_EMBED_HOST_SETUP" >"$GPU_ENGINE_DIR/host_setup.py"
  printf '%s' "$GPU_EMBED_BIOS_GUIDE" >"$GPU_ENGINE_DIR/bios_guide.py"
  printf '%s' "$GPU_EMBED_BIOS_PATHS" >"$GPU_ENGINE_DIR/bios_paths.json"
  printf '%s' "$GPU_EMBED_IGPU_ENGINE" >"$GPU_ENGINE_DIR/igpu_engine.py"
  printf '%s' "$GPU_EMBED_INTEL_DEVICES" >"$GPU_ENGINE_DIR/intel_devices.txt"
  printf '%s' "$GPU_EMBED_IGPU_FIRSTBOOT" >"$GPU_ENGINE_DIR/igpu_firstboot.sh"
  printf '%s' "$GPU_EMBED_IGPU_STAGE" >"$GPU_ENGINE_DIR/igpu_stage.sh"
  printf '%s' "$GPU_EMBED_IGPU_PLIST" >"$GPU_ENGINE_DIR/igpu_launchd.plist"
}

# host_setup.py <command>; JSON on stdin, JSON on stdout. GPU_ROOT redirects it for tests.
function gpu_host() { gpu_engine_init && python3 "$GPU_ENGINE_DIR/host_setup.py" "$@"; }
function gpu_bios() { gpu_engine_init && BIOS_DATA="$GPU_ENGINE_DIR/bios_paths.json" python3 "$GPU_ENGINE_DIR/bios_guide.py" "$@"; }

# JSON helper: gpu_json '<expr using d>' reads JSON on stdin and prints the expression.
function gpu_json() { python3 -c 'import json,sys; d=json.load(sys.stdin); print('"$1"')'; }


# Mirrors gpu.with_global_args for one "-global key=value" argument.
function gpu_with_global_arg() {
  local a="$1" arg="$2" key re
  key="${arg%%=*}"; key="${key#-global }"
  re="-global[[:space:]]+${key//./\\.}=[^[:space:]]+"
  if [[ "$a" =~ $re ]]; then a="${a/"${BASH_REMATCH[0]}"/$arg}"; else a="$a $arg"; fi
  echo "$a" | sed 's/^ *//; s/ *$//'
}

function gpu_without_global_arg() {
  local a="$1" arg="$2" key re
  key="${arg%%=*}"; key="${key#-global }"
  re="[[:space:]]*-global[[:space:]]+${key//./\\.}=[^[:space:]]+"
  if [[ "$a" =~ $re ]]; then a="${a/"${BASH_REMATCH[0]}"/}"; fi
  echo "$a" | sed 's/^ *//; s/ *$//'
}

# ── AMD allowlist (data embedded above; mirrors amd_host.py) ──

# Prints "key|label|reset|boot_args|tahoe" for a supported device id (decimal); rc 1 otherwise.
function amd_family_for() {
  local dev="$1" key label reset args tahoe ids tok lo hi kind real fake fam target=""
  while IFS='|' read -r kind real fake fam; do
    [ "$kind" == "spoof" ] || continue
    (( 16#$real == dev )) && target="$fam"
  done <<<"$GPU_EMBED_AMD_DEVICES"
  while IFS='|' read -r key label reset args tahoe ids; do
    case "$key" in ''|'#'*|spoof|deny) continue ;; esac
    if [ -n "$target" ] && [ "$key" == "$target" ]; then echo "${key}|${label}|${reset}|${args}|${tahoe}"; return 0; fi
    for tok in $ids; do
      lo="${tok%-*}"; hi="${tok#*-}"
      if (( dev >= 16#$lo && dev <= 16#$hi )); then echo "${key}|${label}|${reset}|${args}|${tahoe}"; return 0; fi
    done
  done <<<"$GPU_EMBED_AMD_DEVICES"
  return 1
}

function amd_supported_device() { amd_family_for "$1" >/dev/null; }

function amd_unsupported_reason() {
  local dev="$1" kind label ids reason tok lo hi
  while IFS='|' read -r kind label ids reason; do
    [ "$kind" == "deny" ] || continue
    for tok in $ids; do
      lo="${tok%-*}"; hi="${tok#*-}"
      if (( dev >= 16#$lo && dev <= 16#$hi )); then echo "$reason"; return 0; fi
    done
  done <<<"$GPU_EMBED_AMD_DEVICES"
  echo "$GPU_AMD_GENERIC_REASON"
}

# Extra hostpci option that makes the guest see a supported id, e.g. ",device-id=0x73bf".
function amd_hostpci_extra() {
  local dev="$1" kind real fake fam
  while IFS='|' read -r kind real fake fam; do
    [ "$kind" == "spoof" ] || continue
    (( 16#$real == dev )) && { printf ',device-id=0x%s' "${fake,,}"; return 0; }
  done <<<"$GPU_EMBED_AMD_DEVICES"
  return 0
}

# kind|headline|detail for one card (vendor: amd|nvidia, device id decimal). Mirrors gpu_setup.verdict.
function gpu_verdict() {
  local vendor="$1" dev="$2" fam key label reset args tahoe tail
  if [ "$vendor" == "amd" ]; then
    if ! fam=$(amd_family_for "$dev"); then
      echo "unsupported|Not supported by macOS|$(amd_unsupported_reason "$dev")"; return 0
    fi
    IFS='|' read -r key label reset args tahoe <<<"$fam"
    tail="macOS 13 to 26"
    [ "$tahoe" == "ok" ] || tail="macOS 13 to 15 (26 not confirmed)"
    echo "native|Works natively on ${tail}|${label}"
  elif [ "$vendor" == "intel" ]; then
    igpu_verdict "$dev"
  elif nullmoth_supported_device "$dev"; then
    echo "nullmoth|Works with NullMoth (beta) on macOS 15.5 to 26|Needs the third-party NullMoth driver, which you install yourself."
  else
    echo "unsupported|Not supported by macOS|${GPU_PRE_TURING_REASON}"
  fi
}

# Warnings for an AMD card that passed the generic checks. Mirrors amd_host.check_amd_gpu.
function amd_extra_warnings() {
  local dev="$1" fam key label reset args tahoe
  fam=$(amd_family_for "$dev") || return 0
  IFS='|' read -r key label reset args tahoe <<<"$fam"
  [ "$key" == "vega20" ] && GPU_WARNS+=("$GPU_AMD_VEGA20_WARNING")
  [ "$tahoe" == "unverified" ] && GPU_WARNS+=("${label}: no report of this family on macOS Tahoe 26 yet. Ventura to Sequoia are fine.")
  return 0
}

# Preflight for one AMD card. $1 = bdf (normalized). Fills GPU_ISSUES / GPU_WARNS / GPU_HINTS.
function amd_check() {
  local bdf="$1" sys="${GPU_SYSFS:-/sys}" dev n_before=${#GPU_ISSUES[@]}
  gpu_check "$bdf" 4098 amd_supported_device "" 0 amd_unsupported_reason
  [ ${#GPU_ISSUES[@]} -eq "$n_before" ] || return 0
  dev=$(gpu_sys_num "$sys/bus/pci/devices/$bdf.0/device") || return 0
  amd_extra_warnings "$dev"
}

# ── VM bookkeeping: what the VM looked like before the attach ──

function gpu_record_path() { echo "$GPU_STATE_DIR/gpu-attach-$1.json"; }

# $1 vmid $2 bdf $3 vendor $4 hostpci0 value to be written $5 the profile's -global arg $6 vga_none yes|no $7 oc
# Writes the record (the VM before the attach, plus what the attach will write) through the shared engine.
# A record of the same VM is kept: it holds the true originals. One left by a deleted VM is replaced.
function gpu_record_write() {
  local path cfg
  path=$(gpu_record_path "$1")
  mkdir -p "$GPU_STATE_DIR" || return 1
  cfg=$(qm config "$1" 2>/dev/null) || return 1
  python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"path": a[1], "vmid": int(a[2]), "bdf": a[3], "vendor": a[4], "hostpci": a[5], "global_args": [x for x in a[6].split("\n") if x], "vga_none": a[7] == "yes", "oc": a[8], "cfg_raw": sys.stdin.read()}))' \
    "$path" "$1" "$2" "$3" "$4" "$5" "$6" "$7" <<<"$cfg" | gpu_host record-write >/dev/null
}

# Load the record into REC_* variables. rc 1 when there is none.
function gpu_record_load() {
  local out
  # shellcheck disable=SC2034  # read by the callers
  REC_FOUND=no; REC_BDF=""; REC_VENDOR=""; REC_OC=""
  out=$(python3 - "$(gpu_record_path "$1")" <<'GPU_RECORD_PY'
import json, shlex, sys
try:
    r = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(1)
def put(name, value):
    print("REC_%s=%s" % (name, shlex.quote(value)))
put("FOUND", "yes")
put("BDF", str(r.get("bdf", "")))
put("VENDOR", str(r.get("vendor", "")))
put("OC", str(r.get("oc", "")))
GPU_RECORD_PY
  ) || return 1
  eval "$out"
}

# Same file as rollback.create_snapshot: the VM config before any change.
function gpu_snapshot() {
  local vmid="$1" dir file cfg
  dir="${GPU_SNAPSHOT_BASE:-${ORIG_PWD:-$PWD}}/generated/snapshots"
  mkdir -p "$dir" || return 1
  file="$dir/vm-${vmid}-$(date -u +%Y%m%dT%H%M%SZ).conf"
  if cfg=$(qm config "$vmid" 2>/dev/null); then printf '%s' "$cfg" >"$file"; else printf '# No existing VM config captured\n' >"$file"; fi
  echo "$file"
}

# Mount the VM's ide0 (OpenCore) disk, run the boot-args patcher, unmount.
# $1 vmid, $2 attach|detach|restore, $3 boot-args (attach only).
function gpu_oc_boot_args() {
  local nkept=0 vmid="$1" action="$2" args="${3:-}" vol path loop mnt rc=1 _i old_trap
  vol=$(qm config "$vmid" 2>/dev/null | sed -n 's/^ide0: \([^,]*\).*/\1/p')
  [ -n "$vol" ] || { echo "ERROR: no ide0 (OpenCore) disk found"; return 1; }
  path=$(pvesm path "$vol" 2>/dev/null) || return 1
  loop=$(losetup -fP --show "$path" 2>/dev/null) || return 1
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    partprobe "$loop" 2>/dev/null || true
    [ -b "${loop}p1" ] && break
    sleep 1
  done
  mnt=$(mktemp -d)
  old_trap=$(trap -p INT TERM)
  # shellcheck disable=SC2064
  trap "umount '$mnt' 2>/dev/null; losetup -d '$loop' 2>/dev/null; rmdir '$mnt' 2>/dev/null; exit 130" INT TERM
  if [ -b "${loop}p1" ] && mount "${loop}p1" "$mnt" 2>/dev/null; then
    nkept=$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)
    if GPU_BOOT_ARGS="$args" python3 - "$mnt/EFI/OC" "$action" <<<"$GPU_EMBED_OC_BOOT_ARGS"; then
      rc=0
    fi
    [ "$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)" -le "$nkept" ] || GPU_OC_KEPT=$((${GPU_OC_KEPT:-0} + 1))
    sync
    umount "$mnt" 2>/dev/null || true
  fi
  losetup -d "$loop" 2>/dev/null || true
  rmdir "$mnt" 2>/dev/null || true
  trap - INT TERM
  [ -z "$old_trap" ] || eval "$old_trap"
  return "$rc"
}

# ── AMD attach and the generic undo ──

# `qm set` with a readable failure. Prints nothing on success.
function gpu_qm_set() {
  local vmid="$1" what="$2"
  shift 2
  qm set "$vmid" "$@" >/dev/null || { msg_error "qm set ${what} failed"; return 1; }
}

# Attach an AMD card. $1 vmid $2 bdf, then --display gpu|console, --dry-run, --stop-vm.
# Mirrors gpu_attach.prepare_attach_amd.
function amd_attach() {
  local vmid="${1:-}" gpu="${2:-}" display="gpu" dry=no stop=no bdf cfg status args new_args dev hp0 line extra boot_args vga
  shift 2 2>/dev/null || shift $#
  while [ $# -gt 0 ]; do
    case "$1" in
      --display) display="${2:-gpu}"; shift ;;
      --dry-run) dry=yes ;;
      --stop-vm) stop=yes ;;
      *) msg_error "Unknown option: $1"; return 1 ;;
    esac
    shift
  done
  [[ "$vmid" =~ ^[0-9]+$ ]] && bdf=$(gpu_normalize_bdf "$gpu") || { msg_error "Usage: amd_attach <vmid> <pci-address> [--display gpu|console]"; return 1; }
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  if ! status=$(qm status "$vmid" 2>/dev/null); then
    GPU_ISSUES+=("VM $vmid not found.")
  else
    cfg=$(qm config "$vmid" 2>/dev/null)
    grep -q '^args:.*isa-applesmc' <<<"$cfg" || GPU_ISSUES+=("VM $vmid is not a macOS VM made by this tool (no isa-applesmc in its args).")
    grep -q '^ide2:' <<<"$cfg" && GPU_ISSUES+=("VM $vmid still has the recovery disk attached, so the install is not finished. Finish it, run post-install, then attach the card.")
    if grep -q 'status: running' <<<"$status" && [ "$stop" != "yes" ]; then
      GPU_ISSUES+=("VM $vmid is running. Stop it first: qm stop $vmid")
    fi
    amd_check "$bdf"
    hp0=$(sed -n 's/^hostpci0: //p' <<<"$cfg")
    if [ -n "$hp0" ] && ! grep -qE "^(host=)?(0000:)?${bdf#0000:}(\.[0-7])?(,|;|\$)" <<<"$hp0"; then
      GPU_ISSUES+=("hostpci0 is already in use on this VM (${hp0}). Remove it first or run gpu-setup --undo.")
    fi
    if [ ${#GPU_ISSUES[@]} -eq 0 ]; then
      while read -r line; do
        [ -z "$line" ] && continue
        if [ "${line#* }" == "running" ]; then GPU_ISSUES+=("The card is in use by running VM ${line%% *}.")
        else GPU_WARNS+=("Also assigned to VM ${line%% *} (stopped). Only one of them can run at a time; Proxmox refuses to start the second."); fi
      done < <(gpu_users "$vmid" "$bdf")
    fi
  fi
  nullmoth_print_findings
  [ ${#GPU_ISSUES[@]} -eq 0 ] || return 1
  dev=$(gpu_sys_num "${GPU_SYSFS:-/sys}/bus/pci/devices/$bdf.0/device") || return 1
  args=$(sed -n 's/^args: //p' <<<"$cfg")
  new_args=$(gpu_with_global_arg "$args" "$GPU_HOTPLUG_ARG")
  extra=$(amd_hostpci_extra "$dev")
  boot_args=$(amd_family_for "$dev" | cut -d'|' -f4)
  vga=$(sed -n 's/^vga: //p' <<<"$cfg")
  if [ "$dry" == "yes" ]; then
    echo -e "\nDRY RUN - nothing is changed. Plan:"
    [ "$stop" == "yes" ] && echo "  - Stop VM $vmid if it is running"
    [ -z "$boot_args" ] || echo "  - Add the boot-args '${boot_args}' to the OpenCore config (backup config.plist.pre-gpu)"
    echo "  - qm set $vmid --hostpci0 ${bdf},pcie=1${extra}"
    [ "$new_args" == "$args" ] || echo "  - qm set $vmid --args '$new_args'"
    [ "$display" != "gpu" ] || echo "  - qm set $vmid --vga none"
    return 0
  fi
  if grep -q 'status: running' <<<"$status"; then
    qm stop "$vmid" >/dev/null || { msg_error "Could not stop VM $vmid"; return 1; }
  fi
  qm status "$vmid" 2>/dev/null | grep -q 'status: stopped' || { msg_error "VM $vmid is no longer stopped"; return 1; }
  gpu_snapshot "$vmid" >/dev/null || { msg_error "Could not save the VM config snapshot"; return 1; }
  local oc=""
  [ -z "$boot_args" ] || oc="gpu"
  # The record goes first: it only writes a sidecar file, and a failure later on still finds it.
  gpu_record_write "$vmid" "$bdf" amd "${bdf},pcie=1${extra}" "$GPU_HOTPLUG_ARG" "$([ "$display" == "gpu" ] && echo yes || echo no)" "$oc" || { msg_error "Could not record the original VM settings"; return 1; }
  if [ -n "$boot_args" ]; then
    gpu_oc_boot_args "$vmid" attach "$boot_args" || { msg_error "Could not patch the OpenCore disk; the VM was not changed"; return 1; }
    msg_ok "OpenCore boot-args added (backup: config.plist.pre-gpu)"
  fi
  gpu_qm_set "$vmid" "--hostpci0" --hostpci0 "${bdf},pcie=1${extra}" || return 1
  msg_ok "Attached ${bdf} (all functions) as hostpci0"
  if [ "$new_args" != "$args" ]; then
    gpu_qm_set "$vmid" "--args" --args "$new_args" || return 1
    msg_ok "Added the ACPI hotplug flag to args"
  fi
  if [ "$display" == "gpu" ]; then
    gpu_qm_set "$vmid" "--vga" --vga none || return 1
    msg_ok "Turned the emulated display off (vga none): use the monitor on the card"
  fi
}

# Undo any GPU attach from its record. Only what the attach wrote is reverted: a field changed since keeps the
# user's value and is reported (the shared engine decides). The OpenCore backup is restored by the patcher that
# made it, unless config.plist changed since. $1 vmid, $2 dry-run yes|no, $3 stop-vm yes|no.
# Mirrors gpu.build_hostpci_detach_steps (with a record) and gpu_attach.prepare_detach.
function gpu_detach_record() {
  local vmid="$1" dry="${2:-no}" stop="${3:-no}" cfg plan line op key value
  cfg=$(qm config "$vmid" 2>/dev/null) || { msg_error "VM $vmid not found."; return 1; }
  gpu_record_load "$vmid" || return 2
  if [ -z "${cfg//[[:space:]]/}" ]; then msg_error "Could not read the config of VM $vmid, so nothing was changed. Try again."; return 1; fi
  if [ "$(python3 -c 'import json,sys; print(json.dumps({"record": json.load(open(sys.argv[1])), "cfg_raw": sys.stdin.read()}))' "$(gpu_record_path "$vmid")" <<<"$cfg" | gpu_host vm-stale)" == "1" ]; then
    msg_error "The saved attach record for VM $vmid was written for a different VM (it was deleted and created again), so nothing was reverted. Check its hostpci0, args and vga by hand, then remove $(gpu_record_path "$vmid")."; return 1
  fi
  if ! qm status "$vmid" 2>/dev/null | grep -q 'status: stopped'; then
    if [ "$stop" == "yes" ] && [ "$dry" != "yes" ]; then
      qm stop "$vmid" >/dev/null || { msg_error "Could not stop VM $vmid"; return 1; }
    elif [ "$dry" != "yes" ]; then
      msg_error "VM $vmid is running. Stop it first: qm stop $vmid"; return 1
    fi
  fi
  plan=$(python3 -c 'import json,sys; print(json.dumps({"record": json.load(open(sys.argv[1])), "cfg_raw": sys.stdin.read()}))' "$(gpu_record_path "$vmid")" <<<"$cfg" | gpu_host vm-restore) || return 1
  if [ "$dry" == "yes" ]; then
    echo -e "\nDRY RUN - nothing is changed. Plan:"
    case "$REC_OC" in nullmoth) echo "  - Restore the previous OpenCore config from config.plist.pre-nullmoth (kept if it changed since)" ;; gpu) echo "  - Restore the previous OpenCore config from config.plist.pre-gpu (kept if it changed since)" ;; igpu) echo "  - Restore the previous OpenCore config from config.plist.pre-igpu (kept if it changed since)" ;; esac
    while IFS=$'\t' read -r op key value; do
      [ -z "$op" ] && continue
      if [ "$op" == "set" ]; then echo "  - qm set $vmid --$key '$value'"; else echo "  - qm set $vmid --delete $key"; fi
    done < <(gpu_json '"\n".join("%s\t%s\t%s" % (a["op"], a["key"], a.get("value", "")) for a in d["actions"])' <<<"$plan")
    return 0
  fi
  case "$REC_OC" in
    nullmoth) nullmoth_oc_patch "$vmid" restore || { msg_error "Could not restore the OpenCore config. The VM was not changed."; return 1; } ;;
    gpu) gpu_oc_boot_args "$vmid" restore || { msg_error "Could not restore the OpenCore config. The VM was not changed."; return 1; } ;;
    igpu) igpu_oc_patch "$vmid" restore || { msg_error "Could not restore the OpenCore config. The VM was not changed."; return 1; } ;;
  esac
  [ -z "$REC_OC" ] || msg_ok "Checked the OpenCore config"
  while IFS=$'\t' read -r op key value; do
    [ -z "$op" ] && continue
    if [ "$op" == "set" ]; then gpu_qm_set "$vmid" "--$key" "--$key" "$value" || return 1
    else gpu_qm_set "$vmid" "--delete $key" --delete "$key" || return 1; fi
    msg_ok "Restored $key"
  done < <(gpu_json '"\n".join("%s\t%s\t%s" % (a["op"], a["key"], a.get("value", "")) for a in d["actions"])' <<<"$plan")
  gpu_json '"\n".join(d["notes"])' <<<"$plan" | while IFS= read -r line; do [ -z "$line" ] || echo -e "${INFO}${YW}KEPT: ${line}${CL}"; done
  rm -f "$(gpu_record_path "$vmid")"
}

# ── Intel iGPU passthrough (BETA) ──
# Bash twin of igpu_engine.py, intel_host.py, igpu_attach.py, igpu_finish.py and igpu_cli.py. The decisions
# (device list, ROM pick, memory layout, VM settings, payload disk) come from the embedded igpu_engine.py,
# which is the same file the Python package imports.

function igpu_engine() { gpu_engine_init && python3 "$GPU_ENGINE_DIR/igpu_engine.py" "$@"; }

# $1 pin name (whateverxe|amfipass|graphics), $2 field (0 file, 1 url, 2 sha256)
function igpu_pin() { igpu_engine pins | gpu_json "d['pins']['$1'][$2]"; }

IGPU_CACHE="${IGPU_CACHE:-/var/lib/vz/template/iso}"
IGPU_NOTICE="BETA. The host gives up its Intel graphics for good: the screen on the host stays dark and the Proxmox console works only over the network. The VM needs a monitor or an HDMI dummy plug on the host's native HDMI port (LSPCON HDMI and USB-C do not work). The iGPU shares the CPU's power budget, so heavy host load slows it. macOS Tahoe is experimental: restart it from Proxmox, not from inside macOS."
IGPU_TAHOE_NOTE="macOS Tahoe 26 is experimental with the iGPU: every restart and shutdown from inside macOS hangs (WhateverXe issue 62). Restart the VM from Proxmox (qm stop, then qm start) instead."
IGPU_STAGE_TIMEOUT=900
IGPU_FIRSTBOOT_TIMEOUT=1500
IGPU_TAHOE_FIRSTBOOT_TIMEOUT=600

# 0 when the host is ready for the iGPU (nothing printed). Used by the create-a-VM menu.
function igpu_ready_quiet() {
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  igpu_check 2>/dev/null || return 1
  local ok=0; [ ${#GPU_ISSUES[@]} -eq 0 ] || ok=1
  GPU_ISSUES=(); GPU_WARNS=()
  return "$ok"
}

# Preflight for the iGPU. Fills GPU_ISSUES / GPU_WARNS and IGPU_MB, IGPU_ROM_FILE, IGPU_ROM_KEY, IGPU_DEVICE.
# Mirrors intel_host.check_intel_gpu.
function igpu_check() {
  local out
  IGPU_MB=""; IGPU_ROM_FILE=""; IGPU_ROM_KEY=""; IGPU_DEVICE=""
  out=$(igpu_engine check | python3 -c '
import json, shlex, sys
d = json.load(sys.stdin)
for i in d["issues"]:
    print("GPU_ISSUES+=(%s)" % shlex.quote(i))
for w in d["warnings"]:
    print("GPU_WARNS+=(%s)" % shlex.quote(w))
if d["layout"] and d["layout"]["ok"]:
    print("IGPU_MB=%d" % d["layout"]["max_ram_below_4g_mb"])
    print("IGPU_HOST_TOP=%d" % d["layout"]["host_top"])
if d["info"]:
    print("IGPU_ROM_FILE=%s" % shlex.quote(d["info"]["rom_file"]))
    print("IGPU_ROM_KEY=%s" % shlex.quote(d["info"]["rom"]))
    print("IGPU_MODEL=%s" % shlex.quote(d["info"]["model"]))
if d["device"] is not None:
    print("IGPU_DEVICE=%d" % d["device"])
') || return 1
  eval "$out"
}

# kind|headline|detail for an Intel device id (decimal). Mirrors intel_host.verdict.
function igpu_verdict() {
  igpu_engine verdict "$(printf '%x' "$1")" | gpu_json 'd["kind"] + "|" + d["headline"] + "|" + d["detail"]'
}

# 0 when the VM config ($1) names a machine other than pc, or has recovery attached. Fills GPU_ISSUES.
# Mirrors igpu_attach.vm_issues.
function igpu_vm_issues() {
  local vmid="$1" cfg="$2" status="$3" stop="$4" machine
  grep -q '^args:.*isa-applesmc' <<<"$cfg" || GPU_ISSUES+=("VM $vmid is not a macOS VM made by this tool (no isa-applesmc in its args).")
  grep -qE '^(ide2|sata1):' <<<"$cfg" && GPU_ISSUES+=("VM $vmid still has the recovery disk attached, so the install is not finished. Finish it, run post-install, then attach the iGPU.")
  machine=$(sed -n 's/^machine: //p' <<<"$cfg" | cut -d, -f1)
  if [ "$machine" != "pc" ]; then
    GPU_ISSUES+=("VM $vmid uses the ${machine:-default} machine type. The iGPU needs i440fx (pc), and converting an installed VM has not been tested. Create a new VM with: osx-proxmox-next.sh and answer yes to the iGPU question")
  fi
  if grep -q 'status: running' <<<"$status" && [ "$stop" != "yes" ]; then
    GPU_ISSUES+=("VM $vmid is running. Stop it first: qm stop $vmid")
  fi
}

# Download and verify the kext zips, the option ROM and the graphics package into $IGPU_CACHE.
# Prints nothing; sets IGPU_WEX_ZIP IGPU_AMFI_ZIP IGPU_ROM_PATH IGPU_PKG. $1 = rom key.
function igpu_fetch_assets() {
  local rom_key="$1"
  mkdir -p "$IGPU_CACHE" || return 1
  IGPU_WEX_ZIP=$(igpu_engine fetch pin:whateverxe "$IGPU_CACHE") || return 1
  IGPU_AMFI_ZIP=$(igpu_engine fetch pin:amfipass "$IGPU_CACHE") || return 1
  IGPU_ROM_PATH=$(igpu_engine fetch "rom:${rom_key}" "$IGPU_CACHE") || return 1
  IGPU_PKG=$(igpu_engine fetch pin:graphics "$IGPU_CACHE") || return 1
}

# Mount the VM's ide0 (OpenCore) disk, run the iGPU patcher, unmount.
# $1 vmid, $2 attach|restore|fadt|stage|unstage. attach needs IGPU_WEX_ZIP and IGPU_AMFI_ZIP.
function igpu_oc_patch() {
  local nkept=0 vmid="$1" action="$2" vol path loop mnt rc=1 _i old_trap
  vol=$(qm config "$vmid" 2>/dev/null | sed -n 's/^ide0: \([^,]*\).*/\1/p')
  [ -n "$vol" ] || { echo "ERROR: no ide0 (OpenCore) disk found"; return 1; }
  path=$(pvesm path "$vol" 2>/dev/null) || return 1
  loop=$(losetup -fP --show "$path" 2>/dev/null) || return 1
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    partprobe "$loop" 2>/dev/null || true
    [ -b "${loop}p1" ] && break
    sleep 1
  done
  mnt=$(mktemp -d)
  old_trap=$(trap -p INT TERM)
  # shellcheck disable=SC2064
  trap "umount '$mnt' 2>/dev/null; losetup -d '$loop' 2>/dev/null; rmdir '$mnt' 2>/dev/null; exit 130" INT TERM
  if [ -b "${loop}p1" ] && mount "${loop}p1" "$mnt" 2>/dev/null; then
    nkept=$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)
    if [ "$action" == "attach" ]; then
      IGPU_WEX_ZIP="$IGPU_WEX_ZIP" IGPU_WEX_SHA256="$(igpu_pin whateverxe 2)" \
        IGPU_AMFI_ZIP="$IGPU_AMFI_ZIP" IGPU_AMFI_SHA256="$(igpu_pin amfipass 2)" \
        python3 - "$mnt/EFI/OC" "$action" <<<"$GPU_EMBED_OC_IGPU" && rc=0
    else
      python3 - "$mnt/EFI/OC" "$action" <<<"$GPU_EMBED_OC_IGPU" && rc=0
    fi
    [ "$({ ls "$mnt"/EFI/OC/config.plist.pre-*.kept-* 2>/dev/null || true; } | wc -l)" -le "$nkept" ] || GPU_OC_KEPT=$((${GPU_OC_KEPT:-0} + 1))
    sync
    umount "$mnt" 2>/dev/null || true
  fi
  losetup -d "$loop" 2>/dev/null || true
  rmdir "$mnt" 2>/dev/null || true
  trap - INT TERM
  [ -z "$old_trap" ] || eval "$old_trap"
  return "$rc"
}

# The new-VM conversion: i440fx, recovery on sata1, FadtEnableReset. Mirrors igpu_attach.build_fresh_vm_steps.
function igpu_convert_fresh() {
  local vmid="$1" rec
  qm set "$vmid" --machine pc >/dev/null || return 1
  rec=$(qm config "$vmid" | sed -n 's/^ide2: \([^,]*\),.*/\1/p')
  if [ -n "$rec" ]; then
    qm set "$vmid" --delete ide2 >/dev/null && qm set "$vmid" --sata1 "$rec,media=disk" >/dev/null || return 1
  fi
  qm set "$vmid" --boot 'order=sata1;virtio0;ide0' >/dev/null || return 1
  igpu_oc_patch "$vmid" fadt || { msg_error "Could not patch the OpenCore disk"; return 1; }
  msg_ok "VM $vmid is i440fx with the recovery disk on sata1"
}

# Attach the iGPU to a stopped, installed i440fx VM. $1 vmid, then --dry-run, --stop-vm.
# Mirrors igpu_attach.prepare_attach_igpu.
function igpu_attach() {
  local vmid="${1:-}" dry=no stop=no cfg status args new_args hp0 line settings hostpci
  shift 2>/dev/null || shift $#
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=yes ;;
      --stop-vm) stop=yes ;;
      *) msg_error "Unknown option: $1"; return 1 ;;
    esac
    shift
  done
  [[ "$vmid" =~ ^[0-9]+$ ]] || { msg_error "Usage: igpu_attach <vmid> [--dry-run] [--stop-vm]"; return 1; }
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  if ! status=$(qm status "$vmid" 2>/dev/null); then
    GPU_ISSUES+=("VM $vmid not found.")
  else
    cfg=$(qm config "$vmid" 2>/dev/null)
    igpu_vm_issues "$vmid" "$cfg" "$status" "$stop"
    case "$(nullmoth_detect_macos "$cfg" || true)" in
      tahoe) GPU_WARNS+=("$IGPU_TAHOE_NOTE") ;;
      "") GPU_WARNS+=("Cannot tell which macOS this VM runs. The iGPU works on Ventura to Sequoia; Tahoe is experimental.") ;;
    esac
    igpu_check
    hp0=$(sed -n 's/^hostpci0: //p' <<<"$cfg")
    if [ -n "$hp0" ] && ! grep -qE "^(host=)?(0000:)?00:02(\.[0-7])?(,|;|\$)" <<<"$hp0"; then
      GPU_ISSUES+=("hostpci0 is already in use on this VM (${hp0}). Remove it first or run gpu-setup --undo.")
    fi
    if [ ${#GPU_ISSUES[@]} -eq 0 ]; then
      while read -r line; do
        [ -z "$line" ] && continue
        if [ "${line#* }" == "running" ]; then GPU_ISSUES+=("The card is in use by running VM ${line%% *}.")
        else GPU_WARNS+=("Also assigned to VM ${line%% *} (stopped). Only one of them can run at a time; Proxmox refuses to start the second."); fi
      done < <(gpu_users "$vmid" "0000:00:02")
    fi
  fi
  nullmoth_print_findings
  [ ${#GPU_ISSUES[@]} -eq 0 ] || return 1
  settings=$(igpu_engine settings "$IGPU_MB" "$IGPU_ROM_FILE") || return 1
  hostpci=$(gpu_json 'd["hostpci0"]' <<<"$settings")
  args=$(sed -n 's/^args: //p' <<<"$cfg")
  new_args=$(printf '%s' "$(python3 -c 'import json,sys; s=json.loads(sys.argv[1]); print(json.dumps({"args": sys.argv[2], "wanted": s["args_add"]}))' "$settings" "$args")" | igpu_engine merge-args)
  if [ "$dry" == "yes" ]; then
    echo -e "\nDRY RUN - nothing is changed. Plan:"
    [ "$stop" == "yes" ] && echo "  - Stop VM $vmid if it is running"
    echo "  - Add WhateverXe, AMFIPass and the iGPU boot-args to OpenCore (backup config.plist.pre-igpu)"
    echo "  - Put ${IGPU_ROM_FILE} in /usr/share/kvm"
    echo "  - qm set $vmid --hostpci0 ${hostpci}"
    echo "  - qm set $vmid --args '$new_args'"
    echo "  - qm set $vmid --vga none"
    return 0
  fi
  igpu_fetch_assets "$IGPU_ROM_KEY" || { msg_error "Could not download or verify the iGPU files"; return 1; }
  if grep -q 'status: running' <<<"$status"; then
    qm stop "$vmid" >/dev/null || { msg_error "Could not stop VM $vmid"; return 1; }
  fi
  qm status "$vmid" 2>/dev/null | grep -q 'status: stopped' || { msg_error "VM $vmid is no longer stopped"; return 1; }
  gpu_snapshot "$vmid" >/dev/null || { msg_error "Could not save the VM config snapshot"; return 1; }
  # The record goes first: it only writes a sidecar file, and a failure later on still finds it.
  gpu_record_write "$vmid" "0000:00:02" intel "$hostpci" "$(gpu_json '"\n".join(d["args_add"])' <<<"$settings")" yes igpu \
    || { msg_error "Could not record the original VM settings"; return 1; }
  igpu_oc_patch "$vmid" attach || { msg_error "Could not patch the OpenCore disk; the VM was not changed"; return 1; }
  msg_ok "OpenCore set up for the iGPU (backup: config.plist.pre-igpu)"
  install -m 644 "$IGPU_ROM_PATH" "/usr/share/kvm/$IGPU_ROM_FILE" || { msg_error "Could not install the option ROM"; return 1; }
  gpu_qm_set "$vmid" "--hostpci0" --hostpci0 "$hostpci" || return 1
  gpu_qm_set "$vmid" "--args" --args "$new_args" || return 1
  gpu_qm_set "$vmid" "--vga" --vga none || return 1
  msg_ok "Attached the iGPU (hostpci0, legacy IGD mode, vga none)"
}

# Poll until the VM is stopped. $1 vmid, $2 timeout seconds. rc 1 on timeout.
function igpu_wait_stopped() {
  local vmid="$1" timeout="$2" t0
  t0=$(date +%s)
  while (( $(date +%s) - t0 < timeout )); do
    qm status "$vmid" 2>/dev/null | grep -q 'status: stopped' && return 0
    sleep 10
  done
  return 1
}

# Import a raw image as a disk of the VM and print the volume id. $1 vmid $2 file $3 storage
function igpu_import_disk() {
  local vmid="$1" file="$2" storage="$3" out ref cmd=(qm importdisk)
  qm disk import --help >/dev/null 2>&1 && cmd=(qm disk import)
  out=$("${cmd[@]}" "$vmid" "$file" "$storage" --format raw 2>&1) || { echo "$out" >&2; return 1; }
  ref=$(printf '%s\n' "$out" | sed -n "s/.*successfully imported disk '\([^']\+\)'.*/\1/p" | tr -d "\r\"'")
  [ -n "$ref" ] || { echo "$out" >&2; return 1; }
  echo "$ref"
}

# Detach a slot of the VM and delete the volume it held. $1 vmid $2 slot
function igpu_drop_disk() {
  local vmid="$1" slot="$2" ref key
  ref=$(qm config "$vmid" 2>/dev/null | sed -n "s/^${slot}: \([^,]*\).*/\1/p")
  [ -n "$ref" ] || return 0
  qm set "$vmid" --delete "$slot" >/dev/null 2>&1 || true
  key=$(qm config "$vmid" 2>/dev/null | sed -n "s/^\(unused[0-9]*\): ${ref//\//\\/}\(,.*\)\?\$/\1/p" | head -1)
  [ -z "$key" ] || qm set "$vmid" --delete "$key" >/dev/null 2>&1 || true
}

# Boot the staging recovery, run the script from the payload disk, wait for the guest to power off.
# $1 vmid $2 payload volume. Mirrors igpu_finish._stage.
function igpu_stage() {
  local vmid="$1" payload_ref="$2" path staged
  igpu_oc_patch "$vmid" stage || { msg_error "Could not patch the OpenCore disk (picker)"; return 1; }
  qm start "$vmid" >/dev/null || { msg_error "Could not start VM $vmid"; return 1; }
  unattended_open_recovery_terminal "$vmid" || return 1
  msg_info "Staging the graphics package inside macOS (offline)"
  unattended_type "$vmid" "$(igpu_engine stage-command)"
  sleep 1
  qm sendkey "$vmid" ret >/dev/null 2>&1
  if ! igpu_wait_stopped "$vmid" "$IGPU_STAGE_TIMEOUT"; then
    qm stop "$vmid" >/dev/null 2>&1 || true
    msg_error "The staging script did not finish within $((IGPU_STAGE_TIMEOUT / 60)) minutes"
    return 1
  fi
  path=$(pvesm path "$payload_ref" 2>/dev/null) || return 1
  staged=$(igpu_engine result "$path")
  [ "$staged" == "staged" ] || { msg_error "Staging failed: ${staged:-the script left no result}. Is the macOS disk encrypted (FileVault)?"; return 1; }
  msg_ok "Staged the graphics package"
}

# Stage the graphics package, attach the iGPU and run the first boot. $1 vmid, $2 macos (optional).
# The VM must be stopped, installed and i440fx. Mirrors igpu_finish.finish.
function igpu_finish() {
  local vmid="${1:-}" ver="${2:-}" cfg storage recovery_src payload payload_ref="" rec_ref="" timeout result leftover rc=0
  [[ "$vmid" =~ ^[0-9]+$ ]] || { msg_error "Usage: osx-proxmox-next.sh igpu-finish <vmid> [macos]"; return 1; }
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  cfg=$(qm config "$vmid" 2>/dev/null) || { msg_error "VM $vmid not found."; return 1; }
  igpu_vm_issues "$vmid" "$cfg" "$(qm status "$vmid")" no
  grep -q '^hostpci0:' <<<"$cfg" && GPU_ISSUES+=("VM $vmid already has a PCI device on hostpci0. Run gpu-setup --undo first, then try again.")
  [ -n "$ver" ] || ver=$(nullmoth_detect_macos "$cfg" || true)
  [ -n "$ver" ] || GPU_ISSUES+=("Cannot tell which macOS this VM runs. Pass it: igpu-finish $vmid <ventura|sonoma|sequoia|tahoe>")
  igpu_check
  nullmoth_print_findings
  [ ${#GPU_ISSUES[@]} -eq 0 ] || return 1
  echo -e "${INFO}${YW}${IGPU_NOTICE}${CL}"
  igpu_fetch_assets "$IGPU_ROM_KEY" || { msg_error "Could not download or verify the iGPU files"; return 1; }
  storage=$(sed -n 's/^\(virtio0\|efidisk0\|ide0\): \([^:,]*\):.*/\2/p' <<<"$cfg" | head -1)
  [ -n "$storage" ] || { msg_error "Could not tell which storage the VM disks are on"; return 1; }
  gpu_snapshot "$vmid" >/dev/null || { msg_error "Could not save the VM config snapshot"; return 1; }
  payload="$TEMP_DIR/igpu-payload-${vmid}.img"
  msg_info "Building the payload disk (graphics package, first-boot script)"
  igpu_engine payload "$IGPU_PKG" "$payload" 2>/dev/null || { msg_error "Could not build the payload disk"; return 1; }
  recovery_src="$TEMP_DIR/igpu-recovery-${vmid}.img"
  download_recovery "$ver" "$recovery_src"
  payload_ref=$(igpu_import_disk "$vmid" "$payload" "$storage") || { msg_error "Could not import the payload disk"; return 1; }
  rec_ref=$(igpu_import_disk "$vmid" "$recovery_src" "$storage") || { msg_error "Could not import the recovery disk"; igpu_drop_disk_ref "$vmid" "$payload_ref"; return 1; }
  qm set "$vmid" --sata1 "$rec_ref,media=disk" --sata2 "$payload_ref,media=disk" --boot 'order=sata1;virtio0;ide0' >/dev/null || rc=1
  if [ "$rc" == 0 ]; then
    [ "$(sed -n 's/^vga: //p' <<<"$cfg")" == "std" ] || [ -z "$(sed -n 's/^vga: //p' <<<"$cfg")" ] || qm set "$vmid" --vga std >/dev/null
    leftover=$(printf '%s' "$(python3 -c 'import json,sys; print(json.dumps({"args": sys.argv[1], "wanted": json.loads(sys.argv[2])["args_add"]}))' "$(sed -n 's/^args: //p' <<<"$cfg")" "$(igpu_engine settings 0 x)")" | igpu_engine strip-args)
    [ "$leftover" == "$(sed -n 's/^args: //p' <<<"$cfg")" ] || qm set "$vmid" --args "$leftover" >/dev/null
    igpu_stage "$vmid" "$payload_ref" || rc=1
  fi
  igpu_drop_disk "$vmid" sata1
  igpu_oc_patch "$vmid" unstage || true
  if [ "$rc" != 0 ]; then
    igpu_drop_disk "$vmid" sata2
    rm -f "$payload"
    return 1
  fi
  restore_picker_timeout "$vmid" || msg_info "Could not restore the picker's auto-boot; run post-install afterwards"
  igpu_attach "$vmid" || { msg_error "Attaching the iGPU failed. Undo with: gpu-setup --undo"; return 1; }
  qm set "$vmid" --boot 'order=ide0;virtio0' >/dev/null
  msg_info "First boot with the iGPU: the guest installs the graphics package and powers off"
  timeout=$IGPU_FIRSTBOOT_TIMEOUT
  [ "$ver" == "tahoe" ] && timeout=$IGPU_TAHOE_FIRSTBOOT_TIMEOUT
  qm start "$vmid" >/dev/null || { msg_error "Could not start VM $vmid"; return 1; }
  if ! igpu_wait_stopped "$vmid" "$timeout"; then
    qm stop "$vmid" >/dev/null 2>&1 || true
    if [ "$ver" == "tahoe" ]; then echo -e "${INFO}${YW}${IGPU_TAHOE_NOTE}${CL}"
    else echo -e "${INFO}${YW}The guest did not power off within $((timeout / 60)) minutes, so it was stopped.${CL}"; fi
    igpu_wait_stopped "$vmid" 120 || true
  fi
  result=$(igpu_engine result "$(pvesm path "$payload_ref" 2>/dev/null)" 2>/dev/null || true)
  igpu_drop_disk "$vmid" sata2
  rm -f "$payload"
  qm start "$vmid" >/dev/null 2>&1 || true
  if [ "$result" == "installed" ]; then
    msg_ok "Done. VM $vmid has Metal acceleration. Plug a monitor or an HDMI dummy plug into the host's native HDMI port: the macOS screen appears there, the Proxmox console stays blank."
    return 0
  fi
  echo -e "${INFO}${YW}The graphics package is not installed yet: ${result:-the guest reported nothing}. The VM is usable without acceleration and tries again on its next boot (three tries at most).${CL}"
  return 1
}

# After the unattended install of a new iGPU VM: stop it and run the finish. $1 vmid, $2 macos.
function igpu_after_install() {
  local vmid="$1" ver="$2"
  qm stop "$vmid" >/dev/null 2>&1 || true
  unattended_wait_stopped "$vmid" || true
  msg_info "macOS is installed. Setting up the iGPU"
  igpu_finish "$vmid" "$ver" || echo -e "\n${INFO}${YW}The iGPU setup did not finish. Run: osx-proxmox-next.sh igpu-finish ${vmid} ${ver}${CL}"
}

# Delete an imported volume that was never attached (a failed finish). $1 vmid $2 volume id
function igpu_drop_disk_ref() {
  local vmid="$1" ref="$2" key
  key=$(qm config "$vmid" 2>/dev/null | sed -n "s/^\(unused[0-9]*\): ${ref//\//\\/}\(,.*\)\?\$/\1/p" | head -1)
  [ -z "$key" ] || qm set "$vmid" --delete "$key" >/dev/null 2>&1 || true
}

# The one line shown for a recommended iGPU card. $1 = a GPU_CARDS line. Mirrors GpuCard.detected_line.
function igpu_detected_line() {
  local device driver model reboot=" One host reboot needed."
  IFS='|' read -r _ _ device _ _ _ _ _ driver _ <<<"$1"
  model=$(igpu_engine lookup "$(printf '%x' "$device")" | gpu_json 'd.get("model", "")')
  [ "$driver" != "vfio-pci" ] || reboot=""
  printf '%s (%04x) detected: full macOS acceleration. The host gives up its local screen.%s' "${model:-Intel graphics}" "$device" "$reboot"
}

# 0 when a GPU_CARDS line is a supported Intel iGPU nothing running holds. Mirrors GpuCard.recommended.
function igpu_card_recommended() {
  local vname kind
  IFS='|' read -r _ vname _ _ kind _ <<<"$1"
  [ "$vname" == "intel" ] && [ "$kind" != "unsupported" ] && gpu_card_selectable "$1"
}

# `osx-proxmox-next.sh igpu-check`
function igpu_check_command() {
  GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=()
  igpu_check
  if [ -n "${IGPU_DEVICE:-}" ]; then
    IFS='|' read -r _ head detail <<<"$(igpu_verdict "$IGPU_DEVICE")"
    printf '%s (8086:%04x)\n%s\n%s\n' "${IGPU_MODEL:-Intel graphics}" "$IGPU_DEVICE" "$head" "$detail"
  fi
  nullmoth_print_findings
  if [ ${#GPU_ISSUES[@]} -eq 0 ]; then
    printf 'Host graphics memory ends at 0x%X; the VM gets max-ram-below-4g=%sM and ROM %s.\nThe host is ready.\n' "$IGPU_HOST_TOP" "$IGPU_MB" "$IGPU_ROM_FILE"
    return 0
  fi
  return 2
}

# ── The walkthrough ──

function gpu_say() { echo -e "$*"; }

# Whether to use whiptail screens: a terminal, whiptail installed, and not --yes.
function gpu_use_ui() { [ "${GPU_YES:-no}" != "yes" ] && [ -t 0 ] && [ -t 1 ] && command -v whiptail >/dev/null 2>&1; }

function gpu_ui_text() {
  local title="$1" text="$2"
  if gpu_use_ui; then whiptail --backtitle "OSX Proxmox Next - GPU passthrough" --title "$title" --scrolltext --msgbox "$text" 24 100
  else gpu_say "\n${BOLD}${title}${CL}\n${text}"; fi
}

# Yes/no. With --yes the answer is yes; with no terminal it is no.
function gpu_ui_yesno() {
  local title="$1" text="$2"
  [ "${GPU_YES:-no}" == "yes" ] && return 0
  if gpu_use_ui; then whiptail --backtitle "OSX Proxmox Next - GPU passthrough" --title "$title" --yesno "$text" 20 90
  elif [ -t 0 ]; then local reply; read -r -p "$text [y/N] " reply; [[ "$reply" =~ ^[Yy] ]]
  else return 1; fi
}

# Typed confirmation; never answered by --yes.
function gpu_ui_typed() {
  local text="$1" phrase="$2" reply
  [ -t 0 ] || return 1
  read -r -p "${text} Type '${phrase}' to continue: " reply
  [ "$reply" == "$phrase" ]
}

# Progress rail text for the current state. $1 = state JSON.
function gpu_rail() {
  python3 - "$1" <<'GPU_RAIL_PY'
import json, sys
st = json.loads(sys.argv[1])
steps = ["Scan", "Choose", "BIOS", "Host", "Reboot", "Verify", "VM", "First boot"]
names, marks = [], []
for i, n in enumerate(steps, 1):
    label = "[%d %s]" % (i, n)
    mark = "skipped" if i in st["skipped"] else "done" if i in st["done"] else "<now>" if i == st["step"] else ""
    names.append(label)
    marks.append(mark.center(len(label)))
print(" " + " ".join(names))
print(" " + " ".join(marks).rstrip())
GPU_RAIL_PY
}

function gpu_state() { gpu_host state </dev/null; }
function gpu_state_get() { gpu_state | gpu_json "$1"; }
function gpu_state_save() { printf '%s' "$1" | gpu_host state-save; }
function gpu_step_complete() { printf '{"step": %s, "skipped": %s}' "$1" "${2:-false}" | gpu_host step; }
function gpu_step_goto() { printf '{"step": %s, "goto": true}' "$1" | gpu_host step; }

# One line about the host. Mirrors Session.summary_line (PVE is left out when the version is unknown).
function gpu_summary_line() {
  gpu_host facts </dev/null | gpu_json '"   ".join(["CPU: %s" % d["cpu_vendor"].upper(), "Board: %s" % (" ".join(x for x in (d["board_vendor"], d["board_name"]) if x) or "unknown board"), "Boot loader: %s" % d["bootloader"]] + (["PVE " + __import__("re").search(r"pve-manager/(\d+\.\d+)", d["pve"]).group(1)] if __import__("re").search(r"pve-manager/(\d+\.\d+)", d["pve"] or "") else []) + ["IOMMU: %s" % ("on" if d["iommu_groups"] else "off"), "Boot: %s" % ("UEFI" if d["efi"] else "CSM/legacy")])'
}

# Scan the display adapters. Fills GPU_CARDS with one line per AMD/NVIDIA card:
# bdf|vendor|device(dec)|name|kind|headline|detail|boot_vga|driver|users(comma list "vmid:state")|risky(comma list)|lxc(comma list "ctid:node")|host_console(0|1)
# Usable cards come first. A "|" in the card name is replaced so it cannot shift the fields.
function gpu_scan() {
  local sys="${GPU_SYSFS:-/sys}" dev bdf vendor device cls vname v_kind v_head v_detail boot driver users risky line name seen=" "
  GPU_CARDS=()
  for dev in "$sys"/bus/pci/devices/*; do
    [ -e "$dev" ] || continue
    cls=$(gpu_sys_num "$dev/class") || continue
    (( cls >> 16 == 3 )) || continue
    vendor=$(gpu_sys_num "$dev/vendor") || continue
    bdf=$(basename "$dev"); bdf="${bdf%.*}"
    [[ "$seen" == *" $bdf "* ]] && continue
    case "$vendor" in 4098) vname=amd ;; 4318) vname=nvidia ;; 32902) vname=intel ;; *) continue ;; esac
    seen+="$bdf "
    device=$(gpu_sys_num "$sys/bus/pci/devices/$bdf.0/device" 2>/dev/null || gpu_sys_num "$dev/device") || continue
    IFS='|' read -r v_kind v_head v_detail <<<"$(gpu_verdict "$vname" "$device")"
    boot=$(gpu_sys_num "$sys/bus/pci/devices/$bdf.0/boot_vga" 2>/dev/null || echo 0)
    driver=""
    [ -e "$sys/bus/pci/devices/$bdf.0/driver" ] && driver=$(basename "$(readlink -f "$sys/bus/pci/devices/$bdf.0/driver")")
    users=""
    if [ "$v_kind" != "unsupported" ]; then
      while read -r line; do
        [ -z "$line" ] && continue
        users+="${users:+,}${line%% *}:${line#* }"
      done < <(gpu_users "${1:--1}" "$bdf")
    fi
    risky=""
    if [ -e "$sys/bus/pci/devices/$bdf.0/iommu_group" ]; then
      local member mc
      for member in "$sys/bus/pci/devices/$bdf.0/iommu_group"/devices/*; do
        member=$(basename "$member")
        [[ "$member" == "$bdf."* ]] && continue
        mc=$(gpu_sys_num "$sys/bus/pci/devices/$member/class") || continue
        case $(( mc >> 16 )) in 1|2) risky+="${risky:+,}$member" ;; esac
      done
    fi
    name=$(lspci -nn -s "$bdf.0" 2>/dev/null | sed -n 's/^[^[]*\[[0-9a-f]\{4\}\]: *\(.*\) \[[0-9a-f]\{4\}:[0-9a-f]\{4\}\].*/\1/p' | head -1)
    name="${name//|/\/}"
    local lxc="" ct node
    if [ "$v_kind" != "unsupported" ]; then
      while read -r ct node; do
        [ -z "$ct" ] && continue
        [ "$node" == "/dev/dri" ] || [ "$vname" == "nvidia" ] || continue
        lxc+="${lxc:+,}${ct}:${node}"
      done < <(gpu_host lxc </dev/null)
    fi
    local console=0
    gpu_host_console "$sys/bus/pci/devices/$bdf.0" && console=1
    GPU_CARDS+=("${bdf}|${vname}|${device}|${name:-${vname^^} GPU}|${v_kind}|${v_head}|${v_detail}|${boot}|${driver}|${users}|${risky}|${lxc}|${console}")
  done
  local -a first=() last=()
  local card
  local -a lead=()
  for card in "${GPU_CARDS[@]}"; do
    if igpu_card_recommended "$card"; then lead+=("$card")
    elif gpu_card_selectable "$card"; then first+=("$card"); else last+=("$card"); fi
  done
  GPU_CARDS=("${lead[@]}" "${first[@]}" "${last[@]}")
}

# Why a GPU_CARDS line cannot be picked (empty when it can). Mirrors GpuCard.why_not.
function gpu_card_why_not() {
  local kind detail users risky u out=""
  IFS='|' read -r _ _ _ _ kind _ detail _ _ users risky _ <<<"$1"
  if [ "$kind" == "unsupported" ]; then echo "$detail"
  elif [ -n "$risky" ]; then echo "Its IOMMU group also holds storage or network devices (${risky//,/, }). Passing the card through would take them from the host. Move the card to another slot."
  else
    for u in ${users//,/ }; do [ "${u#*:}" == "running" ] && out+="${out:+, }VM ${u%%:*} (running)"; done
    [ -z "$out" ] || echo "In use by ${out}"
  fi
}

# Warnings shown on a card, one per line. Mirrors GpuCard.notes.
function gpu_card_notes() {
  local kind boot users console u
  IFS='|' read -r _ _ _ _ kind _ _ boot _ users _ _ console <<<"$1"
  [ "$kind" != "unsupported" ] || return 0
  for u in ${users//,/ }; do
    [ "${u#*:}" == "running" ] || echo "Also assigned to VM ${u%%:*} (stopped). Only one of them can run at a time; Proxmox refuses to start the second."
  done
  if [ "$boot" == "1" ] && [ "$console" != "1" ]; then echo "The firmware shows its boot screen on this card."; fi
}

# The stopped VMs that also list a GPU_CARDS line (space separated ids).
function gpu_card_stopped_users() {
  local users u out=""
  IFS='|' read -r _ _ _ _ _ _ _ _ _ users _ <<<"$1"
  for u in ${users//,/ }; do [ "${u#*:}" == "running" ] || out+="${out:+ }${u%%:*}"; done
  echo "$out"
}

# Print one card in a full-width plain box. $1 = a GPU_CARDS line. Mirrors gpu_setup.card_text.
function gpu_card_text() {
  local bdf vname device name kind head detail boot driver users lxc console flags="" u mark rule c why note
  IFS='|' read -r bdf vname device name kind head detail boot driver users _ lxc console <<<"$1"
  [ "$console" == "1" ] && flags+="host console"
  [ -n "$driver" ] && flags+="${flags:+, }bound to ${driver}"
  for c in ${lxc//,/ }; do flags+="${flags:+, }container ${c%%:*} uses ${c#*:}"; done
  mark="  "; rule="-"
  if gpu_card_selectable "$1"; then mark=">>"; rule="="; fi
  local ident lines
  ident=$(printf '%s   %s %04x:%04x' "$bdf" "$(case "$vname" in nvidia) echo NVIDIA ;; intel) echo Intel ;; *) echo AMD ;; esac)" "$(case "$vname" in nvidia) echo $((0x10DE)) ;; intel) echo $((0x8086)) ;; *) echo $((0x1002)) ;; esac)" "$device")
  lines="${name}"$'\n'"${ident}"
  ! igpu_card_recommended "$1" || lines+=$'\n'"Recommended: $(igpu_detected_line "$1")"
  [ "$kind" == "unsupported" ] || lines+=$'\n'"${head}"$'\n'"${detail}"
  [ -z "$flags" ] || lines+=$'\n'"Status: ${flags}"
  while IFS= read -r note; do [ -z "$note" ] || lines+=$'\n'"Note: ${note}"; done < <(gpu_card_notes "$1")
  why=$(gpu_card_why_not "$1")
  [ -z "$why" ] || lines+=$'\n'"Can't use: ${why}"
  python3 -c '
import sys, textwrap
mark, rule, text = sys.argv[1:4]
width = 76
inner = width - 7
rows = []
for ln in text.split("\n"):
    rows += textwrap.wrap(ln, inner) or [""]
border = "%s +%s+" % (mark, rule * (width - 5))
print("\n".join([border] + ["%s | %s |" % (mark, r.ljust(inner)) for r in rows] + [border]))
' "$mark" "$rule" "$lines"
}

# 0 when a GPU_CARDS line can be picked (supported, no RUNNING VM holds it). A stopped VM only warns.
function gpu_card_selectable() {
  local kind users risky u
  IFS='|' read -r _ _ _ _ kind _ _ _ _ users risky _ <<<"$1"
  [ "$kind" != "unsupported" ] && [ -z "$risky" ] || return 1
  for u in ${users//,/ }; do [ "${u#*:}" == "running" ] && return 1; done
  return 0
}

# Print the host_setup target JSON for $1 bdf, $2 vendor, $3 device (decimal), $4 saved identity JSON (optional).
function gpu_target() {
  local reset=none fam
  if [ "$2" == "amd" ] && fam=$(amd_family_for "$3"); then reset=$(cut -d'|' -f3 <<<"$fam"); fi
  python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"bdf": a[1], "vendor": a[2], "reset": a[3], "identity": json.loads(a[4]) if a[4] else None}))' "$1" "$2" "$reset" "${4:-}" | gpu_host target
}

# The saved choice no longer matches the device at its address: say so, forget the choice, go back to the scan.
function gpu_card_changed() {
  gpu_say "REFUSED: $1" >&2
  gpu_say "Nothing was changed. Run the scan again: $GPU_RESUME_CMD" >&2
  gpu_state_save '{"gpu": null, "vm": null, "verify_retry": false}'
  gpu_step_goto 1
}

# nvidia-detach undid this VM: drop it from the walkthrough's list (only when it is listed).
function gpu_forget_vm() {
  [ -f "$GPU_STATE_DIR/gpu-setup.json" ] || return 0
  [ "$(gpu_state_get "$1"' in d["vm_records"]')" == "True" ] || return 0
  gpu_state_save "$(gpu_state_get 'json.dumps({"vm_records": [v for v in d["vm_records"] if v != '"$1"']})')"
}

# VM ids that have a GPU attach record on disk (written by gpu-setup and nvidia-attach alike), one per line.
function gpu_record_vmids() {
  local f id
  for f in "$GPU_STATE_DIR"/gpu-attach-*.json; do
    [ -e "$f" ] || continue
    id="${f##*/gpu-attach-}"; id="${id%.json}"
    [[ "$id" =~ ^[0-9]+$ ]] && echo "$id"
  done
}

# Undo everything: VMs first, then host files. Mirrors Session.undo. $1 = stop the VMs first (yes|no).
# Nothing to revert (no record, the VM is gone, the attach was refused) counts as done.
function gpu_undo() {
  local stop="${1:-no}" vmid ok=yes res rc kept=0 target moved vmids id nothing=no cfgraw
  GPU_OC_KEPT=0
  if [ "$(gpu_state_get 'd.get("gpu") is not None and bool(d["gpu"].get("identity"))')" == "True" ]; then
    target=$(gpu_chosen_target_raw 2>/dev/null) || target=""
    moved=""
    [ -z "$target" ] || moved=$(gpu_json 'd.get("mismatch", "")' <<<"$target")
    if [ -n "$moved" ]; then
      gpu_say "Note: the card at $(gpu_state_get 'd["gpu"]["bdf"]') is now a different device (your PCI layout changed). Undo only removes what this tool wrote, so nothing there is touched."
    fi
  fi
  # One record format for every entry point: a record on disk counts whoever wrote it.
  vmids=" $(gpu_state_get '" ".join(str(v) for v in d["vm_records"])') "
  for id in $(gpu_record_vmids); do [[ "$vmids" == *" $id "* ]] || vmids+="$id "; done
  for vmid in $vmids; do
    rc=0
    if ! gpu_record_load "$vmid"; then
      msg_ok "VM ${vmid}: nothing to revert (the attach did not change it)"
    elif ! qm status "$vmid" >/dev/null 2>&1; then
      rm -f "$(gpu_record_path "$vmid")"
      msg_ok "VM ${vmid}: it no longer exists, so there is nothing to revert on it"
    elif ! cfgraw=$(qm config "$vmid" 2>/dev/null) || [ -z "${cfgraw//[[:space:]]/}" ]; then
      msg_error "VM ${vmid}: could not read its config, so nothing was changed. Try again."
      rc=1
    elif [ "$(python3 -c 'import json,sys; print(json.dumps({"record": json.load(open(sys.argv[1])), "cfg_raw": sys.stdin.read()}))' "$(gpu_record_path "$vmid")" <<<"$cfgraw" | gpu_host vm-stale)" == "1" ]; then
      mv "$(gpu_record_path "$vmid")" "$(gpu_record_path "$vmid").stale-$(date -u +%Y%m%dT%H%M%S)" 2>/dev/null || rm -f "$(gpu_record_path "$vmid")"
      gpu_say "Skipped the record for VM ${vmid}: the VM changed since the card was attached, so nothing was reverted. Check its hostpci0, args and vga by hand."
    else
      gpu_detach_record "$vmid" no "$stop" || rc=$?
      [ "$rc" == "0" ] && msg_ok "VM ${vmid}: GPU settings restored"
    fi
    if [ "$rc" == "0" ]; then
      gpu_state_save "$(gpu_state_get 'json.dumps({"vm_records": [v for v in d["vm_records"] if v != '"$vmid"']})')"
    else
      ok=no
    fi
  done
  res=$(gpu_host undo </dev/null) || ok=no
  [ -z "$res" ] || kept=$(echo "$res" | gpu_json 'len(d.get("kept", []))')
  kept=$((kept + GPU_OC_KEPT))
  [ -z "$res" ] || echo "$res" | gpu_json '"\n".join(["Restored " + p for p in d["restored"]] + d.get("kept", []) + d["errors"]) + ("\nReboot the host to finish undoing the boot changes." if d["reboot_required"] else "")'
  local rj="${res:-}"; [ -n "$rj" ] || rj='{}'
  if [ -z "${vmids// /}" ] && [ "$(echo "$rj" | gpu_json 'bool(d.get("restored") or d.get("kept") or d.get("errors") or d.get("reboot_required"))')" != "True" ]; then
    nothing=yes
    if [ -e "$GPU_STATE_DIR/gpu-setup.json" ]; then
      gpu_say "Removed an empty setup record; nothing else to undo."
    else
      gpu_say "Nothing to undo: no GPU setup record found."
    fi
    local qid
    for qid in $(qm list 2>/dev/null | awk 'NR>1 && $1 ~ /^[0-9]+$/ {print $1}'); do
      qm config "$qid" 2>/dev/null | grep -q '^hostpci0:' && qm config "$qid" 2>/dev/null | grep -q '^args:.*pci-hole64-size' \
        && gpu_say "VM $qid has NullMoth settings in its config but no record. Undo it with: nvidia-detach $qid"
    done
  fi
  if [ "$ok" == "yes" ]; then
    gpu_host hint-clear </dev/null
    rm -f "$GPU_STATE_DIR/gpu-setup.json"
    if [ "$nothing" == "yes" ] && [ "$kept" == "0" ]; then
      :
    elif [ "$kept" == "0" ]; then
      gpu_say "Everything this setup changed is back as it was."
    else
      gpu_say "Done, except for the files listed as kept above. They stay as you left them, so any vfio-pci binding in them still applies."
    fi
  else
    gpu_say "Some parts could not be undone; run gpu-setup --undo again after fixing the above."
    return 1
  fi
}

# `osx-proxmox-next.sh gpu-setup [--status] [--undo] [--yes] [--vm N] [--gpu BDF] [--display gpu|console]
#                               [--accept-nullmoth-risk] [--stop-vm]`
function gpu_setup() {
  local status=no undo=no vmid="" gpu="" display="" accept=no stop=no
  GPU_YES=no
  while [ $# -gt 0 ]; do
    case "$1" in
      --status) status=yes ;;
      --undo) undo=yes ;;
      --yes) GPU_YES=yes ;;
      --vm) vmid="${2:-}"; shift ;;
      --gpu) gpu="${2:-}"; shift ;;
      --display) display="${2:-}"; shift ;;
      --accept-nullmoth-risk) accept=yes ;;
      --stop-vm) stop=yes ;;
      *) msg_error "Unknown option: $1"; return 2 ;;
    esac
    shift
  done
  [ -z "$vmid" ] || [[ "$vmid" =~ ^[0-9]+$ ]] || { msg_error "--vm needs a number"; return 2; }
  if [ "$status" == "yes" ]; then gpu_status; return; fi
  if [ "$undo" == "yes" ]; then gpu_undo "$([ "$stop" == "yes" ] && echo yes || echo no)"; return; fi
  gpu_host state-save <<<"{\"resume_cmd\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$GPU_RESUME_CMD")}" || return 1
  local fin
  fin=$(gpu_state_get 'd["finished"]')
  if [ "$fin" == "True" ]; then
    if ! gpu_ui_yesno "Setup finished" "Setup is finished. Start a new one?"; then gpu_summary; return 0; fi
    gpu_state_save '{"step": 1, "done": [], "skipped": [], "finished": false, "gpu": null, "vm": null, "reboot_needed": false}'
  fi
  local step rc
  while true; do
    gpu_rail "$(gpu_state)"
    step=$(gpu_state_get 'd["step"]')
    rc=0
    case "$step" in
      1|2) gpu_step_choose "$vmid" "$gpu" "$display" "$accept" || rc=$? ;;
      3) gpu_step_bios || rc=$? ;;
      4) gpu_step_host || rc=$? ;;
      5) gpu_step_reboot || rc=$? ;;
      6) gpu_step_verify || rc=$? ;;
      7) gpu_step_vm "$stop" "$vmid" || rc=$? ;;
      8) gpu_step_first_boot || rc=$? ;;
    esac
    [ "$rc" == "100" ] && continue   # a step moved the state: render the next one
    return "$rc"
  done
}

function gpu_status() {
  local line chosen moved
  gpu_rail "$(gpu_state)"
  gpu_summary_line
  gpu_scan
  for line in "${GPU_CARDS[@]}"; do gpu_card_text "$line"; done
  [ ${#GPU_CARDS[@]} -gt 0 ] || echo "No supported graphics card found."
  chosen=$(gpu_state_get 'd["gpu"] and d["gpu"]["bdf"] or ""')
  [ -n "$chosen" ] || { echo "No setup in progress."; return 0; }
  echo "Chosen card: $(gpu_state_get 'd["gpu"]["name"]') ($chosen), VM: $(gpu_state_get 'd["vm"]')"
  moved=$(gpu_chosen_target_raw | gpu_json 'd.get("mismatch", "")')
  if [ -n "$moved" ]; then echo "WARN: $moved"; return 2; fi
}

function gpu_summary() {
  local vm vmname=""
  vm=$(gpu_state_get 'd["vm"] if d["vm"] is not None else ""')
  [ -z "$vm" ] || vmname=$(qm config "$vm" 2>/dev/null | sed -n 's/^name: //p' | head -n1)
  gpu_state | python3 -c '
import json, sys
d = json.load(sys.stdin)
vm, vmname, doc = sys.argv[1:4]
out = ["Setup summary"]
if d["gpu"]:
    out.append("  Card: %s at %s" % (d["gpu"]["name"], d["gpu"]["bdf"]))
if vm:
    out.append("  VM: " + vm + (" " + vmname if vmname else ""))
out += ["  Changed: " + c["path"] for c in d["host_changes"] if not c.get("reverted")]
if d["gpu"]:
    nxt = {"nvidia": "Next, in macOS: install the NullMoth driver yourself (guide: " + doc + "). We do not ship or support it.",
           "intel": "Next: plug a monitor or an HDMI dummy plug into the native HDMI port and start the VM. The graphics package installs itself on the first boot."}
    out += ["", nxt.get(d["gpu"]["vendor"], "Next: start the VM. macOS uses the card with its own driver."), ""]
out.append("  Undo everything with: gpu-setup --undo")
print("\n".join(out))
' "$vm" "$vmname" "${GPU_DOCS_ROOT}nvidia-nullmoth"
}

# Steps 1 and 2: scan and choose. $1 vm $2 gpu $3 display $4 accept
function gpu_step_choose() {
  local want_vm="$1" want_gpu="$2" display="$3" accept="$4" line bdf vname device name kind head detail boot driver users
  local -a usable=()
  gpu_summary_line
  gpu_scan "${want_vm:--1}"
  for line in "${GPU_CARDS[@]}"; do
    gpu_card_text "$line"
    if gpu_card_selectable "$line"; then usable+=("$line"); fi
  done
  if [ ${#usable[@]} -eq 0 ]; then
    local supported=no
    for line in "${GPU_CARDS[@]}"; do IFS='|' read -r _ _ _ _ kind _ <<<"$line"; [ "$kind" == "unsupported" ] || supported=yes; done
    if [ "$supported" == "yes" ]; then
      gpu_say "No supported card can be used right now. See the 'Can't use' line on each card above, fix that, then run the scan again."
    else
      gpu_say "Nothing here can run macOS graphics. Cards that work: ${GPU_DOCS_ROOT}gpu-passthrough#supported-cards"
    fi
    return 2
  fi
  local chosen="" rec=""
  if [ -z "$want_gpu" ]; then
    for line in "${usable[@]}"; do if igpu_card_recommended "$line"; then rec="$line"; break; fi; done
  fi
  if [ -n "$rec" ]; then
    local rec_text; rec_text=$(igpu_detected_line "$rec")
    gpu_say "$rec_text"
    if gpu_ui_yesno "Use the Intel iGPU?" "${rec_text}\n\nUse it for macOS?"; then
      chosen="$rec"
    elif [ ${#usable[@]} -eq 1 ]; then
      gpu_say "Nothing was changed. Run again and confirm (or pass --yes) to use the Intel iGPU. Nothing is enabled without that confirmation: it takes the host console."
      return 2
    else
      local -a others=()
      for line in "${usable[@]}"; do [ "$line" == "$rec" ] || others+=("$line"); done
      usable=("${others[@]}")
    fi
  fi
  if [ -n "$chosen" ]; then
    :
  elif [ -n "$want_gpu" ]; then
    local want; want=$(gpu_normalize_bdf "$want_gpu") || { msg_error "'$want_gpu' is not a PCI address"; return 2; }
    for line in "${usable[@]}"; do [ "${line%%|*}" == "$want" ] && chosen="$line"; done
    [ -n "$chosen" ] || { gpu_say "$want_gpu is not one of the usable cards above."; return 2; }
  elif [ ${#usable[@]} -eq 1 ]; then
    chosen="${usable[0]}"
  elif gpu_use_ui; then
    local -a menu=()
    for line in "${usable[@]}"; do IFS='|' read -r bdf vname device name _ <<<"$line"; menu+=("$bdf" "$name"); done
    bdf=$(whiptail --title "Choose the card" --menu "Which card goes to macOS?" 20 90 8 "${menu[@]}" 3>&1 1>&2 2>&3) || return 0
    for line in "${usable[@]}"; do [ "${line%%|*}" == "$bdf" ] && chosen="$line"; done
  else
    gpu_say "Pick a card with --gpu <address>."; return 2
  fi
  IFS='|' read -r bdf vname device name kind head detail boot driver users _ <<<"$chosen"
  if [ -n "$(gpu_card_stopped_users "$chosen")" ]; then
    local note
    while IFS= read -r note; do case "$note" in Also*) gpu_say "WARN: $note" ;; esac; done < <(gpu_card_notes "$chosen")
    if ! gpu_ui_yesno "Use this card anyway?" "Another stopped VM also lists this card. Use it anyway?"; then
      gpu_say "Nothing was changed. Run again with --yes (or answer y) to use a card that another stopped VM also lists."
      return 2
    fi
  fi
  if [ "$vname" == "amd" ] && [ -z "$display" ]; then
    display="gpu"
    if gpu_use_ui; then
      display=$(whiptail --title "Display" --menu "Where do you want to see macOS?" 14 90 2 \
        gpu "Monitor on the GPU. Best performance. Plug a screen into the card; the Proxmox console goes blank." \
        console "Keep the Proxmox console. The card speeds up apps and you keep using the web console." 3>&1 1>&2 2>&3) || return 0
    fi
  fi
  if [ -z "$want_vm" ] && gpu_use_ui; then
    want_vm=$(whiptail --title "Which VM?" --inputbox "VM ID of the macOS VM that gets the card.\nLeave empty to set up the host first and pick the VM later." 11 80 "" 3>&1 1>&2 2>&3) || return 0
    [ -z "$want_vm" ] || [[ "$want_vm" =~ ^[0-9]+$ ]] || { gpu_say "REFUSED: '$want_vm' is not a VM ID."; return 2; }
  fi
  # The VM
  local issues=() cfg ver
  if [ -n "$want_vm" ]; then
    if ! cfg=$(qm config "$want_vm" 2>/dev/null); then issues+=("VM $want_vm not found.")
    else
      grep -q '^args:.*isa-applesmc' <<<"$cfg" || issues+=("VM $want_vm is not a macOS VM made by this tool.")
      if [ "$vname" == "intel" ]; then
        GPU_ISSUES=(); igpu_vm_issues "$want_vm" "$cfg" "status: stopped" yes
        local iss; for iss in "${GPU_ISSUES[@]}"; do case "$iss" in *"not a macOS VM"*) ;; *) issues+=("$iss") ;; esac; done
        GPU_ISSUES=()
      fi
      if [ "$vname" == "nvidia" ]; then
        ver=$(nullmoth_detect_macos "$cfg") || ver=""
        if [ -z "$ver" ]; then issues+=("Cannot tell which macOS this VM runs. NVIDIA cards need Sequoia 15.5+ or Tahoe.")
        elif [ "$ver" != "sequoia" ] && [ "$ver" != "tahoe" ]; then issues+=("NVIDIA cards need macOS Sequoia 15.5+ or Tahoe, this VM is ${ver}. Pick another VM or create a Sequoia or Tahoe VM first."); fi
      fi
    fi
  fi
  if [ "$vname" == "intel" ]; then
    display="gpu"
    gpu_say "Intel iGPU: the host gives it up for good (no local console), and the VM needs a monitor or an HDMI dummy plug on the host's native HDMI port. LSPCON HDMI and USB-C ports do not work."
  fi
  if [ "$vname" == "nvidia" ]; then
    display="console"
    gpu_say "NVIDIA: the Proxmox console goes black once the driver loads. Turn on Remote Login and Screen Sharing first, and plug a monitor or a dummy plug into the card."
    nullmoth_acknowledge "$accept" || return 2
  fi
  if [ ${#issues[@]} -gt 0 ]; then
    local i; for i in "${issues[@]}"; do gpu_say "REFUSED: $i"; done
    return 2
  fi
  local ack_json=false ident; [ "$vname" == "nvidia" ] && ack_json=true
  ident=$(printf '{"bdf": "%s"}' "$bdf" | gpu_host identity)
  gpu_state_save "$(python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"gpu": {"bdf": a[1], "vendor": a[2], "device": int(a[3]), "boot_vga": a[4] == "1", "name": a[5], "identity": json.loads(a[9])}, "vm": int(a[6]) if a[6] else None, "display": a[7], "ack_nullmoth": a[8] == "true"}))' "$bdf" "$vname" "$device" "$boot" "$name" "$want_vm" "$display" "$ack_json" "$ident")"
  gpu_step_complete 1; gpu_step_complete 2
  return 100
}

# The chosen card as a host_setup target, after checking the device at its address is still that card.
# rc 2 (message on stderr, choice forgotten, state back at the scan) when it is not.
function gpu_chosen_target_raw() {
  gpu_target "$(gpu_state_get 'd["gpu"]["bdf"]')" "$(gpu_state_get 'd["gpu"]["vendor"]')" "$(gpu_state_get 'd["gpu"]["device"]')" \
    "$(gpu_state_get 'json.dumps(d["gpu"].get("identity")) if d["gpu"].get("identity") else ""')"
}

function gpu_chosen_target() {
  local target moved
  target=$(gpu_chosen_target_raw) || return 1
  moved=$(gpu_json 'd.get("mismatch", "")' <<<"$target")
  if [ -n "$moved" ]; then gpu_card_changed "$moved"; return 2; fi
  echo "$target"
}

# Step 3: BIOS. Returns 100 to continue, 3 when paused for the BIOS.
function gpu_step_bios() {
  local target facts items card running reply
  target=$(gpu_chosen_target) || return $?
  facts=$(gpu_host facts </dev/null) || return 1
  items=$(python3 -c 'import json,sys; print(json.dumps({"facts": json.loads(sys.argv[1]), "target": json.loads(sys.argv[2])}))' "$facts" "$target" | gpu_bios check) || return 1
  python3 - "$items" <<'GPU_ITEMS_PY'
import json, sys
for i in json.loads(sys.argv[1]):
    print("  [%s] %s%s" % ("ok" if i["ok"] else "!!", i["key"], "" if i["ok"] else " - " + i["detail"]))
GPU_ITEMS_PY
  local url qr='[]'
  url=$(python3 -c 'import json,sys; print(json.dumps({"facts": json.loads(sys.argv[1])}))' "$facts" | gpu_bios url)
  # The QR code goes inside the card, so one photo of the screen holds the steps and the code.
  if command -v qrencode >/dev/null 2>&1; then
    qr=$(qrencode -t UTF8 -m 2 -o - "$url" 2>/dev/null | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))') || qr='[]'
  fi
  # The QR code sits beside the steps only when the frame fits the screen (the dialog box is 100 wide), else under them.
  local cols=${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}
  [[ "$cols" =~ ^[0-9]+$ ]] || cols=80
  cols=$((10#$cols))
  if gpu_use_ui && [ "$cols" -gt 96 ]; then cols=96; fi
  card=$(python3 -c 'import json,sys; print(json.dumps({"facts": json.loads(sys.argv[1]), "target": json.loads(sys.argv[2]), "items": json.loads(sys.argv[3]), "save": True, "resume_cmd": sys.argv[4], "qr": json.loads(sys.argv[5]), "max_width": int(sys.argv[6])}))' "$facts" "$target" "$items" "$GPU_RESUME_CMD" "$qr" "$((cols - 1))" | gpu_bios card) || {
    local card_rc=$?
    if [ "$card_rc" -eq 3 ]; then
      gpu_say "Nothing to change in the BIOS."
      gpu_step_complete 3
      return 100
    fi
    msg_error "Could not build the BIOS steps (exit $card_rc). Nothing was marked done."
    return 1
  }
  gpu_ui_text "BIOS steps for this server (take a photo)" "$card"
  gpu_say "$card"
  [ "$qr" != '[]' ] || gpu_say "Same steps with pictures: $url"
  gpu_say "Saved to /root/osx-proxmox-next-bios-steps.txt (print it or cat it from another machine)."
  running=$(qm list 2>/dev/null | python3 -c 'import json,sys; print(json.dumps({"qm_list": sys.stdin.read()}))' | gpu_bios running)
  if [ "${GPU_YES:-no}" != "yes" ] && [ -t 0 ]; then
    if [ "$(gpu_bios firmware <<<'{}')" == "yes" ]; then
      [ -z "$running" ] || gpu_say "These VMs will be shut down by the reboot:\n$running"
      if gpu_ui_yesno "Reboot into the BIOS" "Reboot into the BIOS now?"; then
        gpu_host hint-set </dev/null
        systemctl reboot --firmware-setup
        return 0
      fi
    fi
  fi
  gpu_host hint-set </dev/null
  gpu_say "When you are back in Proxmox, run: $GPU_RESUME_CMD"
  return 3
}

# Step 4: host configuration. Returns 100 to continue.
function gpu_step_host() {
  local target plan preview refusals confirmed=false nc=False
  target=$(gpu_chosen_target) || return $?
  plan=$(python3 -c 'import json,sys; print(json.dumps({"target": json.loads(sys.argv[1]), "opts": {}}))' "$target" | gpu_host plan) || return 1
  refusals=$(gpu_json '"\n".join(d["refusals"])' <<<"$plan")
  if [ -n "$refusals" ]; then
    if grep -q "only display adapter" <<<"$refusals"; then gpu_say "Careful: $refusals"; else gpu_say "REFUSED: $refusals"; fi
    if grep -q "only display adapter" <<<"$refusals" && gpu_ui_typed "This card is the only display the host has." "I understand"; then
      nc=True
      plan=$(python3 -c 'import json,sys; print(json.dumps({"target": json.loads(sys.argv[1]), "opts": {"allow_no_console": True}}))' "$target" | gpu_host plan) || return 1
      refusals=$(gpu_json '"\n".join(d["refusals"])' <<<"$plan")
    fi
    [ -z "$refusals" ] || return 2
  fi
  gpu_json '"\n".join(["Note: " + x for x in d["notes"]] + ["WARN: " + x for x in d["warnings"]] + ["Manual step: " + x for x in d["manual"]] + ["OK: " + x for x in d["skipped"]])' <<<"$plan"
  if [ "$(gpu_json 'd.get("optional") and not (d["changes"] or d["commands"] or d["manual"])' <<<"$plan")" == "True" ] \
      && [ "${GPU_YES:-no}" != "yes" ] && [ "$(gpu_state_get 'd["verify_retry"]')" != "True" ] \
      && gpu_ui_yesno "Optional" "Write the boot-time binding anyway? (optional)"; then
    plan=$(python3 -c 'import json,sys; print(json.dumps({"target": json.loads(sys.argv[1]), "opts": {"allow_no_console": sys.argv[2] == "True", "write_optional": True}}))' "$target" "$nc" | gpu_host plan) || return 1
    refusals=$(gpu_json '"\n".join(d["refusals"])' <<<"$plan")
    if [ -n "$refusals" ]; then gpu_say "REFUSED: $refusals"; return 2; fi
    gpu_json '"\n".join(["Note: " + x for x in d["notes"]] + ["WARN: " + x for x in d["warnings"]] + ["Manual step: " + x for x in d["manual"]])' <<<"$plan"
  fi
  if [ "$(gpu_json 'len(d["changes"]) + len(d["commands"]) + len(d["manual"])' <<<"$plan")" == "0" ]; then
    if [ "$(gpu_state_get 'd["verify_retry"]')" == "True" ]; then
      gpu_state_save '{"verify_retry": false}'
      gpu_host hint-set </dev/null
      gpu_say "The verify step found a problem, but there is nothing left for this tool to change. Fix the item(s) marked !! in the verify list (see the docs), then run: $GPU_RESUME_CMD"
      return 2
    fi
    gpu_step_complete 4 true; gpu_step_complete 5 true
    return 100
  fi
  if [ "$(gpu_json 'len(d["changes"]) + len(d["commands"])' <<<"$plan")" == "0" ]; then
    gpu_say "Nothing to write; follow the manual step above, then run: $GPU_RESUME_CMD"
    return 2
  fi
  preview=$(gpu_host preview <<<"$plan")
  gpu_ui_text "This is exactly what will change" "$preview"
  local hard
  while IFS= read -r hard; do
    [ -z "$hard" ] && continue
    gpu_say "HARD WARNING: $hard"
    gpu_ui_typed "" "I understand" || { gpu_say "Nothing was changed."; return 2; }
    confirmed=true
  done < <(gpu_json '"\n".join(d["hard_warnings"])' <<<"$plan")
  gpu_ui_yesno "Apply" "Apply these changes? Each file is backed up first." || { gpu_say "Nothing was changed."; return 0; }
  local res
  res=$(python3 -c 'import json,sys; print(json.dumps({"plan": json.loads(sys.argv[1]), "confirmed": sys.argv[2] == "true"}))' "$plan" "$confirmed" | gpu_host apply) || {
    if [[ "$(gpu_json 'd["error"]' <<<"$res")" == "The card at "* ]]; then gpu_card_changed "$(gpu_json 'd["error"]' <<<"$res")"; return 2; fi
    gpu_say "FAILED: $(gpu_json 'd["error"] + (" The files were put back." if d["rolled_back"] else "")' <<<"$res")"
    return 1
  }
  gpu_say "Done. Backups are in $(gpu_json 'd["backup_dir"]' <<<"$res"). Undo everything with: gpu-setup --undo"
  gpu_state_save '{"verify_retry": false}'
  gpu_step_complete 4
  [ "$(gpu_state_get 'd["reboot_needed"]')" == "True" ] || gpu_step_complete 5 true
  return 100
}

# Step 5: reboot. Never reboots on its own with --yes.
function gpu_step_reboot() {
  local running
  if [ "$(gpu_state_get 'd["reboot_needed"]')" != "True" ]; then gpu_step_complete 5 true; return 100; fi
  if [ "$(gpu_host reboot-pending </dev/null)" != "yes" ]; then
    gpu_say "The host has rebooted since the changes were made."
    gpu_state_save '{"reboot_needed": false}'
    gpu_step_complete 5
    return 100
  fi
  running=$(qm list 2>/dev/null | python3 -c 'import json,sys; print(json.dumps({"qm_list": sys.stdin.read()}))' | gpu_bios running)
  gpu_say "The host must reboot so the new kernel arguments and the early card binding take effect."
  [ -z "$running" ] || gpu_say "These VMs will be shut down:\n$running"
  gpu_host hint-set </dev/null
  if [ "${GPU_YES:-no}" != "yes" ] && gpu_ui_yesno "Reboot" "Reboot now?"; then
    systemctl reboot
    return 0
  fi
  gpu_say "Reboot when it suits you, then run: $GPU_RESUME_CMD"
  return 4
}

# Step 6: verify.
function gpu_step_verify() {
  local target checks fails fix
  target=$(gpu_chosen_target) || return $?
  checks=$(printf '{"target": %s}' "$target" | gpu_host verify) || return 1
  gpu_json '"\n".join("  [%s] %s%s" % ({"ok": "ok", "fail": "!!", "warn": "??", "info": ".."}[c["status"]], c["label"], (" - " + c["detail"]) if c["detail"] else "") for c in d)' <<<"$checks"
  fails=$(gpu_json 'len([c for c in d if c["status"] == "fail"])' <<<"$checks")
  if [ "$fails" == "0" ]; then
    gpu_state_save '{"reboot_needed": false}'
    gpu_step_complete 5 true; gpu_step_complete 6
    return 100
  fi
  if [ "$(gpu_json 'any("Reboot the host" in c["detail"] for c in d if c["status"] == "fail")' <<<"$checks")" == "True" ]; then
    if [ "$(gpu_host rebooted </dev/null)" == "yes" ]; then
      gpu_say "The host has rebooted but the kernel arguments are still not active. Check that the boot loader picked them up (GRUB: /boot/grub/grub.cfg, systemd-boot: proxmox-boot-tool refresh), then run: $GPU_RESUME_CMD"
      gpu_host hint-set </dev/null
      return 2
    fi
    gpu_host mark-reboot </dev/null
    gpu_say "The change is saved but not active yet. Reboot, then run: $GPU_RESUME_CMD"
    gpu_host hint-set </dev/null
    return 4
  fi
  fix=$(gpu_json '[c for c in d if c["status"] == "fail"][0]["fix"]' <<<"$checks")
  if [ "$fix" == "scan" ]; then gpu_card_changed "$(gpu_json '[c for c in d if c["status"] == "fail"][0]["detail"]' <<<"$checks")"; return 2; fi
  case "$fix" in
    bios) gpu_step_goto 3; gpu_state_save '{"verify_retry": true}'; return 100 ;;
    host) gpu_step_goto 4; gpu_state_save '{"verify_retry": true}'; return 100 ;;
  esac
  gpu_say "Fix the item(s) marked !! above and run: $GPU_RESUME_CMD"
  gpu_host hint-set </dev/null
  return 2
}

# Step 7: attach the card to the VM. $1 = stop (yes|no), $2 = the --vm given on the command line
function gpu_step_vm() {
  local stop="$1" want_vm="${2:-}" vmid vendor bdf display zip flags=() cfg ver issue
  gpu_chosen_target >/dev/null || return $?
  vmid=$(gpu_state_get 'd["vm"] if d["vm"] is not None else ""')
  if [ -z "$vmid" ] && [ -n "$want_vm" ]; then
    if ! cfg=$(qm config "$want_vm" 2>/dev/null); then issue="VM $want_vm not found."
    elif ! grep -q '^args:.*isa-applesmc' <<<"$cfg"; then issue="VM $want_vm is not a macOS VM made by this tool."
    elif [ "$(gpu_state_get 'd["gpu"]["vendor"]')" == "intel" ] && GPU_ISSUES=() && igpu_vm_issues "$want_vm" "$cfg" "status: stopped" yes && [ ${#GPU_ISSUES[@]} -gt 0 ]; then
      issue="${GPU_ISSUES[0]}"
    elif [ "$(gpu_state_get 'd["gpu"]["vendor"]')" == "nvidia" ]; then
      ver=$(nullmoth_detect_macos "$cfg") || ver=""
      case "$ver" in
        sequoia|tahoe) ;;
        "") issue="Cannot tell which macOS this VM runs. NVIDIA cards need Sequoia 15.5+ or Tahoe." ;;
        *) issue="NVIDIA cards need macOS Sequoia 15.5+ or Tahoe, this VM is ${ver}. Pick another VM or create a Sequoia or Tahoe VM first." ;;
      esac
    fi
    if [ -n "${issue:-}" ]; then gpu_say "REFUSED: $issue"; return 2; fi
    gpu_state_save "{\"vm\": $want_vm}"
    vmid="$want_vm"
  fi
  if [ -z "$vmid" ] && [ "$(gpu_state_get 'd["gpu"]["vendor"]')" == "intel" ]; then
    gpu_say "The host is ready. Create the VM with the installer (answer yes to the iGPU question, it installs macOS and the graphics package hands-off), or pick an installed i440fx macOS VM: $GPU_RESUME_CMD --vm <vmid>"
    gpu_host hint-set </dev/null
    return 0
  fi
  if [ -z "$vmid" ]; then
    gpu_say "The host is ready. Pick or create the macOS VM, then run: $GPU_RESUME_CMD --vm <vmid>"
    gpu_host hint-set </dev/null
    return 0
  fi
  vendor=$(gpu_state_get 'd["gpu"]["vendor"]')
  bdf=$(gpu_state_get 'd["gpu"]["bdf"]')
  display=$(gpu_state_get 'd["display"]')
  [ "$stop" != "yes" ] || flags+=(--stop-vm)
  if [ "$vendor" == "nvidia" ] && [ "$(gpu_state_get 'd["ack_nullmoth"]')" != "True" ]; then
    gpu_say "REFUSED: The NullMoth notice has not been acknowledged. Run the scan again: $GPU_RESUME_CMD"
    return 2
  fi
  if [ "$vendor" == "nvidia" ]; then
    nullmoth_print_guest_prep
    gpu_ui_yesno "Before you attach" "${NULLMOTH_GUEST_PREP_CONFIRM}" || { gpu_say "Nothing was changed. Turn them on in macOS, then run: $GPU_RESUME_CMD"; return 2; }
  fi
  gpu_ui_yesno "Attach the card" "Attach the card to VM ${vmid} now? A snapshot of its config is saved first." || { gpu_say "Nothing was changed."; return 0; }
  gpu_chosen_target >/dev/null || return $?   # last check before anything is written
  gpu_state_save "$(gpu_state_get 'json.dumps({"vm_records": d["vm_records"] + ([d["vm"]] if d["vm"] not in d["vm_records"] else [])})')"
  if [ "$vendor" == "nvidia" ]; then
    nvidia_attach "$vmid" "$bdf" --accept-nullmoth-risk --guest-confirmed || return 1
  elif [ "$vendor" == "intel" ]; then
    gpu_say "Before the attach, the VM boots macOS recovery once to put the graphics package inside macOS, so the first boot with the iGPU installs it by itself. This takes about 15 minutes."
    igpu_finish "$vmid" || gpu_say "The graphics package is not installed yet; see above."
  else
    amd_attach "$vmid" "$bdf" --display "$display" "${flags[@]}" || return 1
  fi
  gpu_state_save "$(gpu_state_get 'json.dumps({"vm_records": d["vm_records"] + ([d["vm"]] if d["vm"] not in d["vm_records"] else [])})')"
  gpu_step_complete 7
  return 100
}

# Step 8: first boot.
function gpu_step_first_boot() {
  local vendor display
  vendor=$(gpu_state_get 'd["gpu"]["vendor"]')
  display=$(gpu_state_get 'd["display"]')
  if [ "$vendor" == "intel" ]; then
    echo "Plug a monitor, or an HDMI dummy plug, into the host's native HDMI port (not an LSPCON HDMI or USB-C port) and start the VM. The OpenCore picker and the macOS desktop appear on that screen."
    echo "The Proxmox console stays blank: the iGPU is the only display."
    echo "The first boot installs the graphics package by itself and restarts once. Metal is active afterwards."
  elif [ "$vendor" == "nvidia" ]; then
    echo "Start the VM. Once the NullMoth driver loads, the Proxmox console goes black. Connect a monitor or an HDMI/DisplayPort dummy plug to the card to get a desktop (Screen Sharing mirrors it); SSH always works."
    echo "Checklist inside macOS:"
    nullmoth_print_next_steps
    echo "Driver releases: ${NULLMOTH_URL}/releases"
    echo "Our guide: ${GPU_DOCS_ROOT}nvidia-nullmoth"
    command -v qrencode >/dev/null 2>&1 && qrencode -t ANSIUTF8 "${GPU_DOCS_ROOT}nvidia-nullmoth"
  else
    local where="in the Proxmox console (the card does the acceleration)"
    [ "$display" == "gpu" ] && where="on the monitor plugged into the card"
    echo "Start the VM. You should see the OpenCore picker and then the macOS desktop ${where}."
    if [ "${GPU_YES:-no}" != "yes" ] && [ -t 0 ]; then
      while ! gpu_ui_yesno "First boot" "Do you see the macOS desktop on your monitor?"; do
        gpu_troubleshoot_menu || break
      done
    fi
  fi
  gpu_state_save '{"finished": true}'
  gpu_step_complete 8
  gpu_host hint-clear </dev/null
  gpu_summary
  return 0
}

function gpu_troubleshoot_menu() {
  local pick
  echo "1) No signal  2) Black screen after the Apple logo  3) Works once, then fails after a VM restart  4) Stop"
  read -r -p "Which? " pick
  case "$pick" in
    1) echo "No signal on the monitor:"
       echo "  1. Use an HDMI cable first. DisplayPort outputs have stayed black on several cards."
       echo "  2. Plug the monitor into the card, not the motherboard, and power it on before the VM starts."
       echo "  3. The VM must have 'vga none' (the display setting in the VM step) and the card must be on the"
       echo "     PCI Express port. Run gpu-setup again and pick 'Monitor on the GPU'."
       echo "  4. Reset the OpenCore NVRAM from the picker (Reset NVRAM entry) and start the VM again." ;;
    2) echo "Black screen after the Apple logo:"
       echo "  1. Check that the VM is not using the emulated display: gpu-setup shows 'vga none' for it."
       echo "  2. Navi cards need agdpmod=pikera in the boot-args. This tool adds it; check the VM's OpenCore"
       echo "     disk was patched (gpu-setup --status)."
       echo "  3. Turn Resizable BAR off in the BIOS (some RX 6600 cards stay black with it on)."
       echo "  4. Try HDMI instead of DisplayPort." ;;
    3) echo "Works once, then fails until the host reboots (the AMD reset bug):"
       echo "  1. Polaris, Vega and Navi 10/14 need the vendor-reset module. Run gpu-setup and let the Host step"
       echo "     install it. With Secure Boot on it cannot be installed automatically (the Host step explains why)."
       echo "  2. Shut the VM down from inside macOS (Apple menu > Shut Down), not with 'qm stop'."
       echo "  3. If it still fails, reboot the host between VM runs." ;;
    *) gpu_host hint-set </dev/null; echo "Come back with: $GPU_RESUME_CMD"; return 1 ;;
  esac
}
# ── GPU walkthrough end ──

# Boot recovery from the OpenCore picker and open Terminal from its Utilities menu.
# Mirrors unattended.open_recovery_terminal. rc 1 (message printed) when a phase times out.
function unattended_open_recovery_terminal() {
  local vmid="$1"
  msg_info "Unattended (BETA): waiting for the OpenCore boot picker"
  unattended_wait_frame "$vmid" "$UNATTENDED_PICKER_TIMEOUT" above || {
    msg_error "Unattended: boot picker never appeared"; return 1; }
  sleep 2
  msg_ok "Unattended: booting macOS recovery"

  # One return is not enough: it lands while OpenCanopy is still loading on a
  # slow boot disk and is dropped. Keep pressing while the picker is up.
  msg_info "Unattended: waiting for recovery (takes a few minutes)"
  local resets=0
  while :; do
    unattended_wait_frame "$vmid" "$UNATTENDED_RECOVERY_TIMEOUT" below ret || {
      msg_error "Unattended: recovery never reached its UI"; return 1; }
    unattended_settle_recovery "$vmid" && break
    resets=$((resets + 1))
    if (( resets > UNATTENDED_MAX_RECOVERY_RESETS )); then
      msg_error "Unattended: the guest kept resetting to the boot picker instead of starting recovery"
      return 1
    fi
    msg_info "Unattended: the guest reset to the boot picker instead of starting recovery; selecting recovery again"
  done
  unattended_wait_still "$vmid"
  msg_ok "Unattended: recovery loaded"

  # Utilities menu -> Terminal: ctrl-f2, right x4, down x3, ret
  local key
  for key in ctrl-f2 right right right right down down down ret; do
    qm sendkey "$vmid" "$key" >/dev/null 2>&1
    sleep 0.4
  done
  sleep 10
}

function unattended_install() {
  local vmid="$1" disk_gb="$2" recovery_slot="${3:-ide2}" t0 size reboots=0 last_picker last_change digest seen
  t0=$(date +%s)

  unattended_open_recovery_terminal "$vmid" || return 1

  msg_info "Unattended: erasing the VM disk and starting the installer"
  unattended_type "$vmid" "$(unattended_install_command "$disk_gb")"
  sleep 1
  qm sendkey "$vmid" ret >/dev/null 2>&1
  msg_ok "Unattended: installer launched; waiting for its first reboot"

  # First 1080p frame after launch = the install's first reboot. Detach the
  # recovery disk now (the documented manual flow): from here the picker has
  # a single real entry, so a blind selection can never boot the wrong
  # volume. Walking entries with right+ret proved flaky.
  unattended_wait_first_reboot "$vmid" || {
    msg_error "Unattended: the installer never rebooted"; return 1; }
  msg_ok "Unattended: first reboot reached; detaching recovery"
  qm stop "$vmid" >/dev/null 2>&1
  unattended_wait_stopped "$vmid" || {
    msg_error "Unattended: VM did not stop for the recovery-detach step"; return 1; }
  qm set "$vmid" --delete "$recovery_slot" >/dev/null 2>&1
  restore_picker_timeout "$vmid" || \
    msg_info "Unattended: could not restore the picker's auto-boot; run post-install after the install"
  qm start "$vmid" >/dev/null 2>&1
  msg_ok "Unattended: recovery detached; continuing hands-off (30-60 min)"

  local last_press cycles=0 changed
  last_picker=$(date +%s)
  last_change=$last_picker
  last_press=$((last_picker - UNATTENDED_PICKER_REPRESS))
  seen=""
  while (( $(date +%s) - t0 < UNATTENDED_TOTAL_BUDGET )); do
    size=$(unattended_frame_size "$vmid")
    # macOS wedged behind the boot logo is as quiet as a finished install
    # (1280x800 logo) or as a parked picker (1080p logo). What separates them
    # is motion.
    # Running macOS always moves, if only because the Setup Assistant menu bar
    # ticks its clock every minute; a wedged kernel never redraws.
    digest=$(unattended_probe_hash "$vmid")
    changed=0
    if [ -n "$digest" ] && [ "$digest" != "$seen" ]; then
      seen="$digest"
      last_change=$(date +%s)
      changed=1
    fi
    if (( reboots >= 1 && $(date +%s) - last_change > UNATTENDED_HUNG_STILL )); then
      # The guest's own reboot can wedge in an MP rendezvous with every vCPU
      # spinning and the framebuffer stuck on its last frame. qm reset does
      # not clear that; only a full stop/start does, and it boots the
      # finished install to Setup Assistant in about 100s. Checked before the
      # 1080p branch: a wedge on the 1080p boot logo otherwise reads as a
      # parked picker and gets pressed until the budget runs out, while a
      # real picker moves once a press lands.
      if (( cycles >= UNATTENDED_MAX_POWER_CYCLES )); then
        msg_error "Unattended: macOS appears hung: screen unchanged for $((UNATTENDED_HUNG_STILL / 60)) min"
        return 1
      fi
      cycles=$((cycles + 1))
      msg_info "Unattended: guest appears wedged (screen unchanged for $((UNATTENDED_HUNG_STILL / 60)) min); power-cycling"
      qm stop "$vmid" >/dev/null 2>&1
      unattended_wait_stopped "$vmid" || {
        msg_error "Unattended: VM did not stop for the power cycle"; return 1; }
      qm start "$vmid" >/dev/null 2>&1
      # The restarted guest earns its quiet window again: coming back from a
      # power cycle is not by itself proof the install finished.
      last_change=$(date +%s)
      last_picker=$last_change
      seen=""
    elif (( size > UNATTENDED_PICKER_MIN_BYTES )); then
      last_picker=$(date +%s)
      if (( changed )); then
        reboots=$((reboots + 1))
        sleep 2
        # Single-entry picker (Timeout=0 still waits) or an ignored keystroke
        # on the Apple-logo boot screen; either way safe.
        qm sendkey "$vmid" ret >/dev/null 2>&1
        last_press=$(date +%s)
        msg_ok "Unattended: 1080p frame ${reboots}, confirmed the only boot entry"
        sleep "$UNATTENDED_PICKER_REPRESS"
      elif (( $(date +%s) - last_press >= UNATTENDED_PICKER_REPRESS )); then
        # A byte-identical 1080p frame is a picker still parked on its entry:
        # Timeout=0 waits forever and the first return was dropped. Press
        # again, quietly, so the log keeps one line per boot, not per poll.
        qm sendkey "$vmid" ret >/dev/null 2>&1
        last_press=$(date +%s)
      fi
    # Done needs a screen that moved within DONE_MOTION: Setup Assistant ticks
    # its clock every minute. A guest that wedged a few minutes ago is still
    # under HUNG_STILL but already still; it must wait for the hang guard
    # above, not be reported as a finished install.
    elif (( reboots >= 1 && $(date +%s) - last_picker > UNATTENDED_DONE_QUIET
            && $(date +%s) - last_change <= UNATTENDED_DONE_MOTION )); then
      msg_ok "Unattended: install finished after ${reboots} boot(s)"
      return 0
    fi
    sleep "$UNATTENDED_POLL"
  done
  msg_error "Unattended: install did not finish within the time budget"
  return 1
}

# ── Build OpenCore GPT+ESP disk from source ISO ──
function build_opencore_disk() {
  local source_iso="$1"
  local dest_disk="$2"
  local macos_ver="$3"

  msg_info "Building OpenCore boot disk (GPT+ESP)"

  # Clean up stale loops from previous failed runs
  cleanup_stale_loops "$source_iso"
  cleanup_stale_loops "$dest_disk"

  # Create 1GB blank disk
  dd if=/dev/zero of="$dest_disk" bs=1M count=1024 status=none

  # Partition as GPT with EFI System Partition
  sgdisk -Z "$dest_disk" &>/dev/null
  sgdisk -n 1:0:0 -t 1:EF00 -c 1:OPENCORE "$dest_disk" &>/dev/null

  # Set up loop device for destination (tracked globally for cleanup)
  local dest_loop
  setup_loop dest_loop "$dest_disk" "OpenCore destination disk"
  BUILD_LOOP="$dest_loop"

  # Verify partition exists before formatting
  if [ ! -b "${dest_loop}p1" ]; then
    msg_error "ERROR: ${dest_loop}p1 not found after partprobe"
    echo -e "  Hint: Try running the script again (slow storage)"
    exit 1
  fi

  # Format ESP partition
  mkfs.fat -F 32 -n OPENCORE "${dest_loop}p1" &>/dev/null

  # Mount destination ESP
  local dest_mnt
  dest_mnt=$(mktemp -d)
  safe_mount "${dest_loop}p1" "$dest_mnt"
  BUILD_DEST_MNT="$dest_mnt"

  # Mount source ISO (use blkid to find FAT32 partition for any layout)
  local src_mnt src_loop src_part
  src_mnt=$(mktemp -d)
  setup_loop src_loop "$source_iso" "OpenCore source ISO"
  BUILD_SRC_LOOP="$src_loop"

  # A GPT container or non-FAT partition is an expected probe miss. Do not
  # let it abort the build through xargs/pipefail before reaching the ESP.
  local src_candidate
  src_part=""
  for src_candidate in "$src_loop" "${src_loop}"p*; do
    if [[ "$(blkid -s TYPE -o value "$src_candidate" 2>/dev/null || true)" == "vfat" ]]; then
      src_part="$src_candidate"
      break
    fi
  done
  if [ -n "$src_part" ]; then
    safe_mount "$src_part" "$src_mnt" -o ro
  else
    echo -e "  ${YW}WARN: No vfat partition found on source ISO via blkid, trying raw mount${CL}"
    safe_mount "$src_loop" "$src_mnt" -o ro
  fi
  BUILD_SRC_MNT="$src_mnt"

  # Copy OpenCore files (including hidden files)
  cp -a "$src_mnt"/. "$dest_mnt"/ || {
    msg_error "Failed to copy OpenCore files from source ISO"
    exit 1
  }

  # Validate EFI structure was copied
  if [ ! -d "$dest_mnt/EFI/OC" ]; then
    msg_error "OpenCore ISO does not contain expected EFI/OC directory. ISO may be corrupt."
    umount "$dest_mnt" 2>/dev/null || true
    umount "$src_mnt" 2>/dev/null || true
    losetup -d "$src_loop" 2>/dev/null || true
    losetup -d "$dest_loop" 2>/dev/null || true
    rm -rf "$dest_mnt" "$src_mnt"
    exit 1
  fi

  # Add RestrictEvents.kext (silences MacPro7,1 "Memory Modules Misconfigured")
  local re_zip
  re_zip="$(dirname "$source_iso")/RestrictEvents-1.1.6-RELEASE.zip"
  if [ ! -f "$re_zip" ]; then
    curl -fsSL -o "$re_zip" "https://github.com/acidanthera/RestrictEvents/releases/download/1.1.6/RestrictEvents-1.1.6-RELEASE.zip" \
      || echo -e "  ${YW}WARN: RestrictEvents download failed, memory warning fix skipped${CL}"
  fi
  if [ -f "$re_zip" ]; then
    RE_ZIP="$re_zip" OC_KEXTS="$dest_mnt/EFI/OC/Kexts" python3 -c '
import os, zipfile
z = zipfile.ZipFile(os.environ["RE_ZIP"])
names = [n for n in z.namelist() if n.startswith("RestrictEvents.kext/")]
z.extractall(os.environ["OC_KEXTS"], members=names)
' || echo -e "  ${YW}WARN: RestrictEvents extraction failed, memory warning fix skipped${CL}"
  fi

  # Patch config.plist for VM compatibility
  if [ -f "$dest_mnt/EFI/OC/config.plist" ]; then
    python3 -c "
import plistlib, sys
path = sys.argv[1]
cpu_vendor = sys.argv[2]
apple_svc = sys.argv[3] if len(sys.argv) > 3 else 'false'
serial = sys.argv[4] if len(sys.argv) > 4 else ''
uuid_val = sys.argv[5] if len(sys.argv) > 5 else ''
mlb = sys.argv[6] if len(sys.argv) > 6 else ''
rom = sys.argv[7] if len(sys.argv) > 7 else ''
model = sys.argv[8] if len(sys.argv) > 8 else ''
with open(path, 'rb') as f:
    pl = plistlib.load(f)
# Security
pl.setdefault('Misc', {}).setdefault('Security', {})['ScanPolicy'] = 0
pl['Misc']['Security']['DmgLoading'] = 'Any'
pl['Misc']['Security']['SecureBootModel'] = 'Disabled'
# Lets the user persist 'macOS' as the boot entry (Ctrl+Enter in the picker)
pl['Misc']['Security']['AllowSetDefault'] = True
# Boot - graphical picker with Apple icons. Timeout 0 disables auto-boot: the
# picker lists recovery ahead of the installer, so any non-zero value auto-boots
# recovery on each install reboot and the install silently never finishes.
# post-install restores 15s once recovery is detached. Keep in sync with
# script_renderer.py PICKER_TIMEOUT_INSTALL.
pl['Misc'].setdefault('Boot', {})['Timeout'] = 0
pl['Misc']['Boot']['HideAuxiliary'] = True
pl['Misc']['Boot']['PickerAttributes'] = 17
pl['Misc']['Boot']['PickerMode'] = 'External'
pl['Misc']['Boot']['PickerVariant'] = 'Acidanthera\\\Syrah'
# NVRAM: SIP partially disabled for kext loading
nvram = pl.setdefault('NVRAM', {}).setdefault('Add', {}).setdefault('7C436110-AB2A-4BBB-A880-FE41995C9F82', {})
nvram['csr-active-config'] = b'\x67\x0f\x00\x00'
# revblock=pci is the documented config asking RestrictEvents to block the
# MemorySlotNotification and ExpansionSlotNotification processes behind the
# memory-misconfigured banner. It does NOT actually suppress it on Sequoia,
# see script_renderer.py BOOT_ARGS_BASE. Keep the two in sync.
# NOTE: this block lives inside a double-quoted python3 -c argument, so it must
# never contain a double-quote character. One would end the shell string early
# and shift every positional argument, which broke the installer outright.
nvram['boot-args'] = 'keepsyms=1 debug=0x100 revblock=pci'
nvram['prev-lang:kbd'] = 'en-US:0'.encode()
# NVRAM Delete: purge stale values so Add entries take effect
nv_del = pl.setdefault('NVRAM', {}).setdefault('Delete', {})
nv_del['7C436110-AB2A-4BBB-A880-FE41995C9F82'] = ['csr-active-config', 'boot-args', 'prev-lang:kbd']
pl['NVRAM']['WriteFlash'] = True
# Routes Boot* variables to OpenCore's own storage, so the default entry set
# via AllowSetDefault survives firmware that deletes entries it dislikes
pl.setdefault('UEFI', {}).setdefault('Quirks', {})['RequestBootVarRouting'] = True
# Enable VirtualSMC
[k.update(Enabled=True) for k in pl.get('Kernel', {}).get('Add', []) if 'VirtualSMC' in k.get('BundlePath', '')]
# Register RestrictEvents.kext if present (default revblock=auto blocks the
# MacPro7,1 memory misconfiguration notifier; no boot-arg needed)
import os
ka = pl.setdefault('Kernel', {}).setdefault('Add', [])
kx = os.path.join(os.path.dirname(path), 'Kexts', 'RestrictEvents.kext')
if os.path.isdir(kx) and 'RestrictEvents.kext' not in [k.get('BundlePath') for k in ka]:
    ka.append({'Arch': 'Any', 'BundlePath': 'RestrictEvents.kext',
               'Comment': 'Silence MacPro7,1 memory warning', 'Enabled': True,
               'ExecutablePath': 'Contents/MacOS/RestrictEvents',
               'MaxKernel': '', 'MinKernel': '', 'PlistPath': 'Contents/Info.plist'})
[k.update(Enabled=True) for k in ka if 'RestrictEvents' in k.get('BundlePath', '')]
# AMD-specific patches
if cpu_vendor == 'AMD':
    kq = pl['Kernel']['Quirks']
    kq['AppleCpuPmCfgLock'] = True
    kq['AppleXcpmCfgLock'] = True
# PlatformInfo: required for Apple Services (iMessage, FaceTime, iCloud)
# macOS reads identity from OpenCore's EFI PlatformInfo, not QEMU SMBIOS
if apple_svc == 'true' and serial:
    pi = pl.setdefault('PlatformInfo', {}).setdefault('Generic', {})
    pi['SystemSerialNumber'] = serial
    pi['SystemProductName'] = model
    pi['SystemUUID'] = uuid_val
    pi['MLB'] = mlb
    pi['ROM'] = bytes.fromhex(rom)
    pl['PlatformInfo']['UpdateSMBIOS'] = True
    pl['PlatformInfo']['UpdateDataHub'] = True
# Apple ID VM detection bypass (Sequoia 15+ / Tahoe 26). Swaps two sysctl
# names in the kernel cstring table; keep in sync with script_renderer.py
# _APPLE_ID_BYPASS_PATCHES. test_bash_and_python_emit_identical_kernel_patches
# runs both patchers and diffs the result, so drift here fails the suite:
#   1. rename the real kern.hv_vmm_present OID to hibernatecount
#   2. rename the real kern.hibernatecount OID to hv_vmm_present
# A true swap, so no name is invented and none disappears. The value now
# behind hv_vmm_present is the hibernate counter: 0 at boot, incremented only
# on waking from a sleep that opened a hibernate file. Not a constant; it
# stays 0 because MacPro7,1 defaults to hibernatemode=0.
# Step 1 is not optional: without it both OIDs answer to hv_vmm_present and
# sysctlbyname() still returns the real one (1), so sign-in keeps failing.
if apple_svc == 'true':
    kp = pl.setdefault('Kernel', {}).setdefault('Patch', [])
    for cmt, find_hex, repl_hex in (
        ('Apple ID VM bypass - hide real hv_vmm_present',
         '626f6f742073657373696f6e20555549440068765f766d6d5f70726573656e7400',
         '626f6f742073657373696f6e20555549440068696265726e617465636f756e7400'),
        ('Apple ID VM bypass - hv_vmm_present',
         '68696265726e61746568696472656164790068696265726e617465636f756e7400',
         '68696265726e61746568696472656164790068765f766d6d5f70726573656e7400'),
    ):
        find_b = bytes.fromhex(find_hex)
        kp[:] = [x for x in kp if x.get('Find') != find_b]
        kp.append({'Arch': 'x86_64', 'Base': '', 'Comment': cmt,
                   'Count': 1, 'Enabled': True, 'Find': find_b,
                   'Identifier': 'kernel', 'Limit': 0, 'Mask': b'',
                   'MaxKernel': '', 'MinKernel': '24.0.0',
                   'Replace': bytes.fromhex(repl_hex),
                   'ReplaceMask': b'', 'Skip': 0})
with open(path, 'wb') as f:
    plistlib.dump(pl, f)
" "$dest_mnt/EFI/OC/config.plist" "$CPU_VENDOR" \
      "$APPLE_SERVICES" "$SMBIOS_SERIAL" "$SMBIOS_UUID" "$SMBIOS_MLB" "$SMBIOS_ROM" "$SMBIOS_MODEL" || {
      msg_error "Failed to patch OpenCore config.plist"
      umount "$dest_mnt" 2>/dev/null || true
      umount "$src_mnt" 2>/dev/null || true
      losetup -d "$src_loop" 2>/dev/null || true
      losetup -d "$dest_loop" 2>/dev/null || true
      rm -rf "$dest_mnt" "$src_mnt"
      exit 1
    }
    # Fix plistlib self-closing tags that OpenCore's OcXmlLib rejects
    sed -i 's|<array/>|<array></array>|g; s|<dict/>|<dict></dict>|g; s|<data/>|<data></data>|g' "$dest_mnt/EFI/OC/config.plist"
  fi

  # Hide OC partition from boot picker (shown only when user presses Space)
  echo "Auxiliary" > "$dest_mnt/.contentVisibility"

  # Cleanup mounts
  umount "$src_mnt" 2>/dev/null || true
  umount "$dest_mnt" 2>/dev/null || true
  losetup -d "$BUILD_SRC_LOOP" 2>/dev/null || true
  losetup -d "$dest_loop" 2>/dev/null || true
  rm -rf "$dest_mnt" "$src_mnt"
  BUILD_SRC_MNT="" BUILD_DEST_MNT="" BUILD_LOOP="" BUILD_SRC_LOOP=""

  msg_ok "Built OpenCore boot disk"
}

# pushd needs a resolvable cwd to save onto the dir stack; if the caller's
# shell is sitting in a directory that got removed from under it (e.g. by a
# prior install script deleting its own repo checkout), getcwd() fails and
# pushd aborts. Land somewhere known-good first so that can't happen here.
ORIG_PWD="$PWD"
cd "${HOME:-/root}" 2>/dev/null || cd /

TEMP_DIR=$(mktemp -d)
pushd "$TEMP_DIR" >/dev/null

# `osx-proxmox-next.sh post-install <vmid>`: leave install mode on an existing
# VM, before any of the create-a-VM prompts.
if [ "${1:-}" == "post-install" ]; then
  check_root
  # In a condition so a failed patch exits 1 instead of tripping the ERR trap.
  if post_install "${2:-}"; then exit 0; else exit 1; fi
fi

# `osx-proxmox-next.sh igpu-check` / `igpu-finish <vmid> [macos]`: BETA, Intel iGPU passthrough.
if [ "${1:-}" == "igpu-check" ]; then
  check_root
  if igpu_check_command; then exit 0; else exit 2; fi
fi
if [ "${1:-}" == "igpu-finish" ]; then
  check_root
  if igpu_finish "${@:2}"; then exit 0; else exit 1; fi
fi

# `osx-proxmox-next.sh nvidia-attach <vmid> <pci-address>` / `nvidia-detach <vmid>`:
# BETA, NVIDIA GPU for the third-party NullMoth driver on an installed VM.
if [ "${1:-}" == "nvidia-attach" ]; then
  check_root
  if nvidia_attach "${@:2}"; then exit 0; else exit 1; fi
fi
if [ "${1:-}" == "nvidia-detach" ]; then
  check_root
  if nvidia_detach "${@:2}"; then exit 0; else exit 1; fi
fi

# `osx-proxmox-next.sh gpu-setup [--status|--undo|--yes|--vm N|--gpu BDF|--display gpu|console]`:
# the guided GPU passthrough walkthrough for AMD and NVIDIA cards.
if [ "${1:-}" == "gpu-setup" ]; then
  check_root
  GPU_RC=0
  gpu_setup "${@:2}" || GPU_RC=$?
  exit "$GPU_RC"
fi

if whiptail --backtitle "OSX Proxmox Next" --title "macOS VM" --yesno "This will create a new macOS VM on Proxmox.\n\nRequirements:\n  - Intel or AMD CPU with VT-x/AMD-V\n  - 64GB+ free disk space\n  - Internet access (downloads ~1GB)\n\nProceed?" 14 58; then
  :
else
  header_info && echo -e "${CROSS}${RD}User exited script${CL}\n" && exit
fi

function default_settings() {
  MACOS_VER="sequoia"
  VMID=$(get_valid_nextid)
  DISK_SIZE="$(default_disk_gb "$MACOS_VER")G"
  HN="macos-sequoia"
  CORE_COUNT="$(detect_cpu_cores)"
  RAM_SIZE="$(detect_memory_mb)"
  BRG="vmbr0"
  MAC="$GEN_MAC"
  VLAN=""
  MTU=""
  START_VM="yes"
  UNATTENDED="no"
  IGPU="no"
  METHOD="default"
  echo -e "${OS}${BOLD}${DGN}macOS Version: ${BGN}${MACOS_LABELS[$MACOS_VER]}${CL}"
  echo -e "${CONTAINERID}${BOLD}${DGN}Virtual Machine ID: ${BGN}${VMID}${CL}"
  echo -e "${CONTAINERTYPE}${BOLD}${DGN}Machine Type: ${BGN}q35${CL}"
  echo -e "${DISKSIZE}${BOLD}${DGN}Disk Size: ${BGN}${DISK_SIZE}${CL}"
  echo -e "${LBL_HOSTNAME}${BOLD}${DGN}Hostname: ${BGN}${HN}${CL}"
  echo -e "${CPUCORE}${BOLD}${DGN}CPU Cores: ${BGN}${CORE_COUNT}${CL}"
  echo -e "${RAMSIZE}${BOLD}${DGN}RAM Size: ${BGN}${RAM_SIZE}${CL}"
  echo -e "${BRIDGE}${BOLD}${DGN}Bridge: ${BGN}${BRG}${CL}"
  echo -e "${MACADDRESS}${BOLD}${DGN}MAC Address: ${BGN}${MAC}${CL}"
  echo -e "${VLANTAG}${BOLD}${DGN}VLAN: ${BGN}Default${CL}"
  echo -e "${DEFAULT}${BOLD}${DGN}Interface MTU Size: ${BGN}Default${CL}"
  echo -e "${GATEWAY}${BOLD}${DGN}Start VM when completed: ${BGN}yes${CL}"
  echo -e "${CREATING}${BOLD}${DGN}Creating a macOS VM using the above default settings${CL}"
}

function advanced_settings() {
  METHOD="advanced"

  if MACOS_VER=$(whiptail --backtitle "OSX Proxmox Next" --title "macOS Version" --radiolist "Choose macOS version" --cancel-button Exit-Script 14 58 4 \
    "ventura" "macOS Ventura 13 (stable)  " OFF \
    "sonoma" "macOS Sonoma 14 (stable)  " OFF \
    "sequoia" "macOS Sequoia 15 (stable)  " ON \
    "tahoe" "macOS Tahoe 26 (stable)  " OFF \
    3>&1 1>&2 2>&3); then
    echo -e "${OS}${BOLD}${DGN}macOS Version: ${BGN}${MACOS_LABELS[$MACOS_VER]}${CL}"
    : # version metadata handled by MACOS_LABELS
  else
    exit-script
  fi

  [ -z "${VMID:-}" ] && VMID=$(get_valid_nextid)
  while true; do
    if VMID=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set Virtual Machine ID" 8 58 "$VMID" --title "VIRTUAL MACHINE ID" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
      if [ -z "$VMID" ]; then
        VMID=$(get_valid_nextid)
      fi
      if pct status "$VMID" &>/dev/null || qm status "$VMID" &>/dev/null; then
        echo -e "${CROSS}${RD} ID $VMID is already in use${CL}"
        sleep 2
        continue
      fi
      echo -e "${CONTAINERID}${BOLD}${DGN}Virtual Machine ID: ${BGN}$VMID${CL}"
      break
    else
      exit-script
    fi
  done

  local default_disk
  default_disk=$(default_disk_gb "$MACOS_VER")
  if DISK_SIZE=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set Disk Size in GiB (minimum 64)" 8 58 "$default_disk" --title "DISK SIZE" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    DISK_SIZE=$(echo "$DISK_SIZE" | tr -d ' ')
    if [[ "$DISK_SIZE" =~ ^[0-9]+$ ]]; then
      if [ "$DISK_SIZE" -lt 64 ]; then
        msg_error "Disk size must be at least 64 GiB for macOS"
        exit-script
      fi
      DISK_SIZE="${DISK_SIZE}G"
      echo -e "${DISKSIZE}${BOLD}${DGN}Disk Size: ${BGN}$DISK_SIZE${CL}"
    elif [[ "$DISK_SIZE" =~ ^[0-9]+G$ ]]; then
      local num="${DISK_SIZE%G}"
      if [ "$num" -lt 64 ]; then
        msg_error "Disk size must be at least 64 GiB for macOS"
        exit-script
      fi
      echo -e "${DISKSIZE}${BOLD}${DGN}Disk Size: ${BGN}$DISK_SIZE${CL}"
    else
      msg_error "Invalid Disk Size. Please use a number (e.g., 64 or 64G)."
      exit-script
    fi
  else
    exit-script
  fi

  local default_hn="macos-${MACOS_VER}"
  if VM_NAME=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set Hostname" 8 58 "$default_hn" --title "HOSTNAME" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$VM_NAME" ]; then
      HN="$default_hn"
    else
      HN=$(echo "${VM_NAME,,}" | tr -d ' ')
    fi
    if ! [[ "$HN" =~ ^[a-zA-Z0-9][a-zA-Z0-9.\-]*$ ]]; then
      msg_error "Invalid hostname: $HN (must start with alphanumeric, contain only [a-zA-Z0-9.-])"
      exit-script
    fi
    echo -e "${LBL_HOSTNAME}${BOLD}${DGN}Hostname: ${BGN}$HN${CL}"
  else
    exit-script
  fi

  local default_cores
  default_cores=$(detect_cpu_cores)
  if CORE_COUNT=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Allocate CPU Cores (minimum 2)" 8 58 "$default_cores" --title "CORE COUNT" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$CORE_COUNT" ]; then
      CORE_COUNT="$default_cores"
    fi
    if ! [[ "$CORE_COUNT" =~ ^[0-9]+$ ]]; then
      msg_error "CPU cores must be a number"
      exit-script
    fi
    if [ "$CORE_COUNT" -lt 2 ]; then
      msg_error "At least 2 CPU cores are required for macOS"
      exit-script
    fi
    echo -e "${CPUCORE}${BOLD}${DGN}CPU Cores: ${BGN}$CORE_COUNT${CL}"
  else
    exit-script
  fi

  local default_ram ram_limit ram_hint
  default_ram=$(detect_memory_mb)
  ram_limit=$(max_vm_memory_mb)
  ram_hint="Allocate RAM in MiB (minimum 4096)"
  [ "$ram_limit" -gt 0 ] && ram_hint="Allocate RAM in MiB (minimum 4096, host has ${ram_limit} free)"
  if RAM_SIZE=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "$ram_hint" 8 58 "$default_ram" --title "RAM" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$RAM_SIZE" ]; then
      RAM_SIZE="$default_ram"
    fi
    if ! [[ "$RAM_SIZE" =~ ^[0-9]+$ ]]; then
      msg_error "RAM size must be a number"
      exit-script
    fi
    if [ "$RAM_SIZE" -lt 4096 ]; then
      msg_error "At least 4096 MiB RAM is required for macOS"
      exit-script
    fi
    echo -e "${RAMSIZE}${BOLD}${DGN}RAM Size: ${BGN}$RAM_SIZE${CL}"
  else
    exit-script
  fi

  if BRG=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set a Bridge" 8 58 vmbr0 --title "BRIDGE" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$BRG" ]; then
      BRG="vmbr0"
    fi
    if ! [[ "$BRG" =~ ^[a-zA-Z0-9]+$ ]]; then
      msg_error "Invalid bridge name: $BRG"
      exit-script
    fi
    echo -e "${BRIDGE}${BOLD}${DGN}Bridge: ${BGN}$BRG${CL}"
  else
    exit-script
  fi

  if MAC1=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set a MAC Address" 8 58 "$GEN_MAC" --title "MAC ADDRESS" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$MAC1" ]; then
      MAC="$GEN_MAC"
    else
      MAC="$MAC1"
    fi
    echo -e "${MACADDRESS}${BOLD}${DGN}MAC Address: ${BGN}$MAC${CL}"
  else
    exit-script
  fi

  if VLAN1=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set a VLAN (leave blank for default)" 8 58 --title "VLAN" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$VLAN1" ]; then
      VLAN1="Default"
      VLAN=""
    else
      if ! [[ "$VLAN1" =~ ^[0-9]+$ ]]; then
        msg_error "VLAN must be a number"
        exit-script
      fi
      VLAN=",tag=$VLAN1"
    fi
    echo -e "${VLANTAG}${BOLD}${DGN}VLAN: ${BGN}$VLAN1${CL}"
  else
    exit-script
  fi

  if MTU1=$(whiptail --backtitle "OSX Proxmox Next" --inputbox "Set Interface MTU Size (leave blank for default)" 8 58 --title "MTU SIZE" --cancel-button Exit-Script 3>&1 1>&2 2>&3); then
    if [ -z "$MTU1" ]; then
      MTU1="Default"
      MTU=""
    else
      if ! [[ "$MTU1" =~ ^[0-9]+$ ]]; then
        msg_error "MTU must be a number"
        exit-script
      fi
      MTU=",mtu=$MTU1"
    fi
    echo -e "${DEFAULT}${BOLD}${DGN}Interface MTU Size: ${BGN}$MTU1${CL}"
  else
    exit-script
  fi

  if (whiptail --backtitle "OSX Proxmox Next" --title "START VIRTUAL MACHINE" --yesno "Start VM when completed?" 10 58); then
    echo -e "${DGN}Start VM when completed: ${BGN}yes${CL}"
    START_VM="yes"
  else
    echo -e "${DGN}Start VM when completed: ${BGN}no${CL}"
    START_VM="no"
  fi

  UNATTENDED="no"
  if [ "$START_VM" == "yes" ] && whiptail --backtitle "OSX Proxmox Next" --title "UNATTENDED INSTALL (BETA)" --yesno \
    "Drive the whole macOS install automatically?\n\nThe script boots recovery, ERASES the new VM disk, runs the\ninstaller, and handles every reboot until Setup Assistant.\nTakes 30-60 minutes; leave the VM console alone while it runs.\n\nVerified on Ventura, Sonoma, Sequoia and Tahoe." \
    14 66 --defaultno 3>&1 1>&2 2>&3; then
    UNATTENDED="yes"
    echo -e "${DGN}Unattended install (BETA): ${BGN}yes${CL}"
  else
    echo -e "${DGN}Unattended install (BETA): ${BGN}no${CL}"
  fi

  IGPU="no"
  if [ "$UNATTENDED" == "yes" ] && igpu_ready_quiet && whiptail --backtitle "OSX Proxmox Next" --title "INTEL iGPU (BETA)" --yesno \
    "Pass this host's Intel iGPU through to the VM?\n\nThe VM becomes i440fx, the install runs as usual, and the graphics\npackage is installed for you afterwards (no steps in macOS).\n\nThe host has no screen of its own from then on. The VM needs a\nmonitor or an HDMI dummy plug on the native HDMI port.\nmacOS Tahoe is experimental with the iGPU." \
    17 66 --defaultno 3>&1 1>&2 2>&3; then
    IGPU="yes"
    echo -e "${DGN}Intel iGPU passthrough (BETA): ${BGN}yes${CL}"
  fi

  if (whiptail --backtitle "OSX Proxmox Next" --title "ADVANCED SETTINGS COMPLETE" --yesno "Ready to create ${MACOS_LABELS[$MACOS_VER]} VM?" --no-button Do-Over 10 58); then
    echo -e "${RD}Creating a macOS VM using the above advanced settings${CL}"
  else
    header_info
    echo -e "${RD}Using Advanced Settings${CL}"
    advanced_settings
  fi
}

function start_script() {
  if (whiptail --backtitle "OSX Proxmox Next" --title "SETTINGS" --yesno "Use Default Settings?" --no-button Advanced 10 58); then
    header_info
    echo -e "${BL}Using Default Settings${CL}"
    default_settings
  else
    header_info
    echo -e "${RD}Using Advanced Settings${CL}"
    advanced_settings
  fi
}

check_root
arch_check
pve_check
ssh_check
check_dependencies
ensure_ignore_msrs
start_script

# Both settings paths have set RAM_SIZE by now; refuse to continue when the
# host cannot actually back that allocation (balloon=0 pins it at qm start).
require_vm_memory_fits "$RAM_SIZE"

# ── Storage selection ──
msg_info "Validating Storage"
declare -a STORAGE_MENU=()
MSG_MAX_LENGTH=0
while read -r line; do
  TAG=$(echo "$line" | awk '{print $1}')
  TYPE=$(echo "$line" | awk '{printf "%-10s", $2}')
  FREE=$(echo "$line" | numfmt --field 4-6 --from-unit=K --to=iec --format %.2f 2>/dev/null | awk '{printf( "%9sB", $6)}') || FREE="  unknown"
  ITEM="  Type: $TYPE Free: $FREE "
  OFFSET=2
  if [[ $((${#ITEM} + $OFFSET)) -gt $MSG_MAX_LENGTH ]]; then
    MSG_MAX_LENGTH=$((${#ITEM} + $OFFSET))
  fi
  STORAGE_MENU+=("$TAG" "$ITEM" "OFF")
done < <(pvesm status -content images | awk 'NR>1')
if [ ${#STORAGE_MENU[@]} -eq 0 ]; then
  msg_error "Unable to detect a valid storage location."
  exit
elif [ $((${#STORAGE_MENU[@]} / 3)) -eq 1 ]; then
  STORAGE=${STORAGE_MENU[0]}
else
  while [ -z "${STORAGE:+x}" ]; do
    if ! STORAGE=$(whiptail --backtitle "OSX Proxmox Next" --title "Storage Pools" --radiolist \
      "Which storage pool would you like to use for ${HN}?\nTo make a selection, use the Spacebar.\n" \
      16 $(($MSG_MAX_LENGTH + 23)) 6 \
      "${STORAGE_MENU[@]}" 3>&1 1>&2 2>&3); then
      exit-script
    fi
  done
fi
msg_ok "Using ${CL}${BL}$STORAGE${CL} ${GN}for Storage Location."
msg_ok "Virtual Machine ID is ${CL}${BL}$VMID${CL}."

# ── Apple Services toggle ──
if whiptail --backtitle "OSX Proxmox Next" --title "Apple Services" --yesno \
  "Enable Apple Services (iMessage, FaceTime, iCloud)?\n\nGenerates a unique SMBIOS identity and static MAC, and patches\nOpenCore PlatformInfo plus the Apple ID kernel patches.\n\nThe kernel patches take effect on Sequoia 15 and Tahoe 26 only.\nAfter install, check:  sysctl -n kern.hv_vmm_present\nIt must print 0. If sign-in still fails, the Apple Services\nguide covers the Sonoma fallback." \
  17 70 --defaultno 3>&1 1>&2 2>&3; then
  APPLE_SERVICES="true"
  msg_ok "Apple Services enabled"
else
  msg_ok "Apple Services disabled"
fi

# ── Obtain OpenCore ISO (assemble locally, prebuilt as fallback) ──
CACHE_DIR="/var/lib/vz/template/cache"
mkdir -p "$CACHE_DIR" || { msg_error "Cannot create $CACHE_DIR"; exit 1; }
OC_ISO="$CACHE_DIR/opencore-osx-proxmox-vm.iso"

if [ -f "$OC_ISO" ] && [ -s "$OC_ISO" ]; then
  msg_ok "Using cached OpenCore ISO"
else
  rm -f "$OC_ISO"
  msg_info "Assembling OpenCore boot image from pinned releases"
  if assemble_opencore_iso "$OC_ISO"; then
    msg_ok "Assembled OpenCore boot image"
  else
    echo -e "  ${YW}WARN: local assembly failed, falling back to prebuilt ISO${CL}"
    msg_info "Downloading OpenCore ISO"
    if ! curl -fSL --retry 3 -o "$OC_ISO.part" "$OC_URL" || ! mv "$OC_ISO.part" "$OC_ISO"; then
      msg_error "Failed to download OpenCore ISO from $OC_URL"
      rm -f "$OC_ISO" "$OC_ISO.part"
      exit 1
    fi
    msg_ok "Downloaded OpenCore ISO"
  fi
fi

# ── Download macOS recovery image ──
RECOVERY_RAW="$TEMP_DIR/recovery.img"
download_recovery "$MACOS_VER" "$RECOVERY_RAW"

# ── Generate SMBIOS identity (before OC build, PlatformInfo needs these) ──
generate_smbios "$MACOS_VER"

# ── Apple Services: derive ROM from static MAC ──
if [ "$APPLE_SERVICES" = "true" ]; then
  # Generate static MAC address (locally administered, unicast)
  MAC_BYTE1=$(( (0x$(openssl rand -hex 1) | 0x02) & 0xFE ))
  MAC_BYTE1=$(printf '%02X' $MAC_BYTE1)
  MAC_rest=$(openssl rand -hex 5 | tr '[:lower:]' '[:upper:]' | sed 's/\(..\)/\1:/g; s/:$//')
  STATIC_MAC="${MAC_BYTE1}:${MAC_rest}"
  if ! [[ "$STATIC_MAC" =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]]; then
    msg_error "Failed to generate valid static MAC: $STATIC_MAC"
    exit 1
  fi
  # Derive ROM from MAC (macOS cross-checks ROM against NIC during Apple ID validation)
  SMBIOS_ROM=$(echo "$STATIC_MAC" | tr -d ':')
fi

# ── Build OpenCore GPT disk ──
OC_DISK="$TEMP_DIR/opencore.raw"
build_opencore_disk "$OC_ISO" "$OC_DISK" "$MACOS_VER"

# ── Determine final MAC address (Apple Services uses static MAC) ──
if [ "$APPLE_SERVICES" = "true" ]; then
  VM_MAC="$STATIC_MAC"
else
  VM_MAC="$MAC"
fi

# ── Create VM ──
msg_info "Creating macOS VM shell"
qm create "$VMID" \
  --name "$HN" \
  --ostype other \
  --machine q35 \
  --bios ovmf \
  --sockets 1 \
  --cores "$CORE_COUNT" \
  --memory "$RAM_SIZE" \
  --cpu host \
  --balloon 0 \
  --agent enabled=1 \
  --net0 "vmxnet3,bridge=$BRG,macaddr=$VM_MAC,firewall=0$VLAN$MTU" \
  >/dev/null
msg_ok "Created VM shell"

# ── Apply macOS hardware profile ──
msg_info "Applying macOS hardware profile (CPU: $CPU_VENDOR, emulation: $CPU_NEEDS_EMULATION, HEDT: ${XEON_HEDT_MODEL:-no})"
if [ "$CPU_NEEDS_EMULATION" = "yes" ]; then
  CPU_FLAG="-cpu Cascadelake-Server,vendor=GenuineIntel,+invtsc,-pcid,-hle,-rtm,-avx512f,-avx512dq,-avx512cd,-avx512bw,-avx512vl,-avx512vnni,kvm=on,vmware-cpuid-freq=on"
elif [ -n "$XEON_HEDT_MODEL" ]; then
  CPU_FLAG="-cpu ${XEON_HEDT_MODEL},kvm=on,vendor=GenuineIntel,+invtsc,vmware-cpuid-freq=on"
else
  CPU_FLAG="-cpu host,kvm=on,vendor=GenuineIntel,+kvm_pv_unhalt,+kvm_pv_eoi,+hypervisor,+invtsc,vmware-cpuid-freq=on"
fi
qm set "$VMID" \
  --args "-device isa-applesmc,osk=\"ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc\" -smbios type=2 -device qemu-xhci -device usb-kbd -device usb-tablet -global nec-usb-xhci.msi=off -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off ${CPU_FLAG}" \
  --vga std \
  --tablet 1 \
  --scsihw virtio-scsi-pci \
  >/dev/null
msg_ok "Applied hardware profile ($CPU_VENDOR)"

# ── Set SMBIOS identity (base64-encoded for Proxmox) ──
msg_info "Setting SMBIOS identity"
SMBIOS_SERIAL_B64=$(echo -n "$SMBIOS_SERIAL" | base64 -w 0)
SMBIOS_MFR_B64=$(echo -n "Apple Inc." | base64 -w 0)
SMBIOS_PRODUCT_B64=$(echo -n "$SMBIOS_MODEL" | base64 -w 0)
SMBIOS_FAMILY_B64=$(echo -n "Mac" | base64 -w 0)
qm set "$VMID" \
  --smbios1 "uuid=${SMBIOS_UUID},base64=1,serial=${SMBIOS_SERIAL_B64},manufacturer=${SMBIOS_MFR_B64},product=${SMBIOS_PRODUCT_B64},family=${SMBIOS_FAMILY_B64}" \
  >/dev/null
msg_ok "Set SMBIOS identity (serial: $SMBIOS_SERIAL)"

# ── Apple Services configuration (vmgenid + static MAC) ──
if [ "$APPLE_SERVICES" = "true" ]; then
  msg_info "Configuring Apple Services (iMessage, FaceTime, iCloud)"
  # Generate vmgenid for Apple services
  VMGENID=$(tr '[:lower:]' '[:upper:]' < /proc/sys/kernel/random/uuid)
  # STATIC_MAC was already generated before OC build (for ROM derivation)

  qm set "$VMID" --vmgenid "$VMGENID" >/dev/null
  msg_ok "Configured Apple Services (vmgenid: $VMGENID, MAC: $STATIC_MAC)"
fi

# ── Attach EFI disk + TPM ──
msg_info "Attaching EFI disk + TPM"
qm set "$VMID" \
  --efidisk0 "${STORAGE}:0,efitype=4m,pre-enrolled-keys=0" \
  --tpmstate0 "${STORAGE}:0,version=v2.0" \
  >/dev/null
msg_ok "Attached EFI disk + TPM"

# ── Create main disk ──
msg_info "Creating main disk (${DISK_SIZE})"
qm set "$VMID" --virtio0 "${STORAGE}:${DISK_SIZE%G}" >/dev/null
msg_ok "Created main disk"

# ── Detect import command (PVE 8.x vs 9.x) ──
if qm disk import --help >/dev/null 2>&1; then
  IMPORT_CMD=(qm disk import)
else
  IMPORT_CMD=(qm importdisk)
fi

# Helper: extract disk ref from import output, with fallback
# Args: $1=import_output $2=storage $3=vmid $4=label
function get_disk_ref() {
  local import_out="$1" storage="$2" vmid="$3" label="$4"
  local ref
  ref="$(printf '%s\n' "$import_out" | sed -n "s/.*successfully imported disk '\([^']\+\)'.*/\1/p" | tr -d "\r\"'")"
  if [[ -z "$ref" ]]; then
    # Fallback: get the most recently created disk for this VM
    ref="$(pvesm list "$storage" --vmid "$vmid" 2>/dev/null | awk 'NR>1{print $1}' | sort | tail -n1)"
  fi
  if [[ -z "$ref" ]]; then
    msg_error "Unable to determine imported ${label} disk reference."
    echo "$import_out"
    exit 1
  fi
  echo "$ref"
}

# ── Import and attach OpenCore disk → ide0 ──
msg_info "Importing OpenCore boot disk"
OC_IMPORT_OUT="$("${IMPORT_CMD[@]}" "$VMID" "$OC_DISK" "$STORAGE" --format raw 2>&1)" || {
  msg_error "Failed to import OpenCore disk"
  echo "$OC_IMPORT_OUT"
  exit 1
}
OC_DISK_REF="$(get_disk_ref "$OC_IMPORT_OUT" "$STORAGE" "$VMID" "OpenCore")"
qm set "$VMID" --ide0 "${OC_DISK_REF},media=disk" >/dev/null
# Fix GPT header corruption on thin-provisioned LVM after importdisk
OC_DEV=$(pvesm path "$OC_DISK_REF" 2>/dev/null) || true
if [ -n "$OC_DEV" ] && [ -b "$OC_DEV" ]; then
  dd if="$OC_DISK" of="$OC_DEV" bs=512 count=2048 conv=notrunc 2>/dev/null || true
fi
msg_ok "Attached OpenCore disk (ide0)"

# ── Stamp recovery with Apple icon flavour ──
msg_info "Adding Apple icon flavour to recovery"
MACOS_LABEL="${MACOS_LABELS[$MACOS_VER]}"
# Fix HFS+ dirty/lock flags so Linux mounts read-write
python3 -c "
import struct, subprocess, sys
img = sys.argv[1]
out = subprocess.check_output(['sgdisk', '-i', '1', img], text=True)
start = int([l for l in out.splitlines() if 'First sector' in l][0].split(':')[1].split('(')[0].strip())
off = start * 512 + 1024 + 4
f = open(img, 'r+b'); f.seek(off)
a = struct.unpack('>I', f.read(4))[0]
a = (a | 0x100) & ~0x800
f.seek(off); f.write(struct.pack('>I', a))
f.close(); print('HFS+ flags fixed')
" "$RECOVERY_RAW"
cleanup_stale_loops "$RECOVERY_RAW"
setup_loop RLOOP "$RECOVERY_RAW" "recovery image"
RECOVERY_MNT=$(mktemp -d /tmp/oc-recovery.XXXXXX)
if [ ! -b "${RLOOP}p1" ]; then
  msg_error "ERROR: ${RLOOP}p1 not found after partprobe"
  echo -e "  Hint: Try running the script again (slow storage)"
  exit 1
fi
safe_mount "${RLOOP}p1" "$RECOVERY_MNT" -t hfsplus -o rw
# Write .contentDetails in CoreServices (matches Python planner)
mkdir -p "$RECOVERY_MNT/System/Library/CoreServices"
# NEVER rm first: unlinking on the Linux hfsplus driver can leave a stale
# catalog entry that makes any recreate fail with EEXIST. Overwrite through
# the existing file; the label is cosmetic, so failure must not abort.
printf '%s' "$MACOS_LABEL" > "$RECOVERY_MNT/System/Library/CoreServices/.contentDetails" 2>/dev/null \
  || echo -e "  ${YW}WARN: .contentDetails stamp failed (cosmetic), continuing${CL}"
# Copy InstallAssistant.icns → .VolumeIcon.icns (matches Python planner)
ICON=$(find "$RECOVERY_MNT" -path '*/Install macOS*/Contents/Resources/InstallAssistant.icns' 2>/dev/null | head -1)
if [ -n "$ICON" ]; then
  # cat-overwrite instead of rm+cp: the Linux hfsplus driver sometimes
  # refuses to unlink a .VolumeIcon.icns the BaseSystem already ships
  # (Sonoma does); a cosmetic icon must never fail the install. Keep in
  # sync with planner.py _recovery_steps.
  cat "$ICON" > "$RECOVERY_MNT/.VolumeIcon.icns" 2>/dev/null \
    || echo -e "  ${YW}WARN: volume icon copy failed (cosmetic), continuing${CL}"
fi
umount "$RECOVERY_MNT" 2>/dev/null || umount -l "$RECOVERY_MNT" 2>/dev/null || true
losetup -d "$RLOOP" 2>/dev/null || true
rm -rf "$RECOVERY_MNT"
RLOOP="" RECOVERY_MNT=""
msg_ok "Recovery stamped with Apple icon"

# ── Import and attach recovery disk → ide2 ──
msg_info "Importing macOS recovery disk"
REC_IMPORT_OUT="$("${IMPORT_CMD[@]}" "$VMID" "$RECOVERY_RAW" "$STORAGE" --format raw 2>&1)" || {
  msg_error "Failed to import recovery disk"
  echo "$REC_IMPORT_OUT"
  exit 1
}
REC_DISK_REF="$(get_disk_ref "$REC_IMPORT_OUT" "$STORAGE" "$VMID" "recovery")"
qm set "$VMID" --ide2 "${REC_DISK_REF},media=disk" >/dev/null
msg_ok "Attached recovery disk (ide2)"

# ── Set boot order ──
msg_info "Setting boot order"
qm set "$VMID" --boot order="ide2;virtio0;ide0" >/dev/null
msg_ok "Set boot order (ide2 → virtio0 → ide0)"

# ── VM description ──
DESCRIPTION=$(
  cat <<EOF
<div align='center'>
  <h2 style='font-size: 24px; margin: 20px 0;'>${MACOS_LABELS[$MACOS_VER]} VM</h2>

  <p style='margin: 16px 0;'>
    <a href='https://ko-fi.com/lucidfabrics' target='_blank' rel='noopener noreferrer'>
      <img src='https://img.shields.io/badge/&#x2615;-Buy me a coffee-blue' alt='Buy Coffee' />
    </a>
  </p>

  <span style='margin: 0 10px;'>
    <i class="fa fa-github fa-fw" style="color: #f5f5f5;"></i>
    <a href='https://github.com/lucid-fabrics/osx-proxmox-next' target='_blank' rel='noopener noreferrer' style='text-decoration: none; color: #00617f;'>GitHub</a>
  </span>
  <span style='margin: 0 10px;'>
    <i class="fa fa-comments fa-fw" style="color: #f5f5f5;"></i>
    <a href='https://github.com/lucid-fabrics/osx-proxmox-next/discussions' target='_blank' rel='noopener noreferrer' style='text-decoration: none; color: #00617f;'>Discussions</a>
  </span>
  <span style='margin: 0 10px;'>
    <i class="fa fa-exclamation-circle fa-fw" style="color: #f5f5f5;"></i>
    <a href='https://github.com/lucid-fabrics/osx-proxmox-next/issues' target='_blank' rel='noopener noreferrer' style='text-decoration: none; color: #00617f;'>Issues</a>
  </span>
</div>
EOF
)
qm set "$VMID" -description "$DESCRIPTION" >/dev/null
msg_ok "Created ${MACOS_LABELS[$MACOS_VER]} VM ${CL}${BL}(${HN})"

# ── Intel iGPU: i440fx shape, recovery on sata1, FadtEnableReset ──
if [ "${IGPU:-no}" == "yes" ]; then
  igpu_convert_fresh "$VMID" || { msg_error "Could not prepare the VM for the iGPU"; exit 1; }
fi

# ── Start VM ──
if [ "$START_VM" == "yes" ]; then
  msg_info "Starting macOS VM"
  # Clean up any stale swtpm processes for this VMID
  pkill -f "swtpm.*/${VMID}\\.swtpm" 2>/dev/null || true
  rm -f "/var/run/qemu-server/${VMID}.swtpm" "/var/run/qemu-server/${VMID}.swtpm.pid" 2>/dev/null
  sleep 1
  qm start "$VMID"
  msg_ok "Started macOS VM"
fi

# ── Unattended install (BETA) ──
if [ "${UNATTENDED:-no}" == "yes" ] && [ "$START_VM" == "yes" ]; then
  DISK_GB_NUM="${DISK_SIZE%G}"
  IGPU_SLOT=ide2
  [ "${IGPU:-no}" != "yes" ] || IGPU_SLOT=sata1
  if unattended_install "$VMID" "$DISK_GB_NUM" "$IGPU_SLOT"; then
    UNATTENDED_DONE=yes
    [ "${IGPU:-no}" != "yes" ] || igpu_after_install "$VMID" "$MACOS_VER"
    echo -e "\n${INFO}${GN}Unattended install complete. Finish Setup Assistant in the VM console.${CL}"
  else
    SHOT=$(unattended_save_frame "$VMID") && \
      echo -e "\n${INFO}${YW}The console at that moment was saved to ${SHOT}.${CL}"
    echo -e "\n${INFO}${YW}Unattended install did not finish; continue manually in the VM console.${CL}"
  fi
fi

echo ""
msg_ok "Completed successfully!"
# After a finished unattended run the manual recovery steps would contradict it.
if [ "${UNATTENDED_DONE:-no}" != "yes" ]; then
  echo -e "\n${INFO}${YW}Next steps:${CL}"
  echo -e "  1. Open the VM console (VM ${VMID} → Console)"
  echo -e "  2. The picker waits for a choice; pick the macOS recovery entry"
  echo -e "  3. Use Disk Utility to erase the VirtIO disk as APFS"
  echo -e "  4. Run 'Reinstall macOS' from the recovery menu"
  echo -e "  5. When the installer reboots the first time, stop the VM and run:"
  echo -e "     bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/scripts/bash/osx-proxmox-next.sh)\" -- post-install ${VMID}"
  echo -e "     It detaches recovery and makes the picker auto-boot again."
fi
echo -e ""
echo -e "  ${BL}Documentation: https://github.com/lucid-fabrics/osx-proxmox-next${CL}"
echo -e ""
