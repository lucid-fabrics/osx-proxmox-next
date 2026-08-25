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

  src_part=$(blkid -o device "$src_loop" "${src_loop}"p* 2>/dev/null \
    | xargs -I{} sh -c 'blkid -s TYPE -o value {} 2>/dev/null | grep -q vfat && echo {}' \
    | head -1)
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
cd "${HOME:-/root}" 2>/dev/null || cd /

TEMP_DIR=$(mktemp -d)
pushd "$TEMP_DIR" >/dev/null

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

echo ""
msg_ok "Completed successfully!"
echo -e "\n${INFO}${YW}Next steps:${CL}"
echo -e "  1. Open the VM console (VM ${VMID} → Console)"
echo -e "  2. The installer auto-boots after 15 seconds (Apple logo boot screen)"
echo -e "  3. Use Disk Utility to erase the VirtIO disk as APFS"
echo -e "  4. Run 'Reinstall macOS' from the recovery menu"
echo -e ""
echo -e "  ${BL}Documentation: https://github.com/lucid-fabrics/osx-proxmox-next${CL}"
echo -e ""
