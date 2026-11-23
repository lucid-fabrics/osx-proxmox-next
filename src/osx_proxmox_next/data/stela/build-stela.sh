#!/usr/bin/env bash
# Builds Stela from one pinned commit. Nothing of Stela is vendored here: the source is fetched at run time.
#
#   build-stela.sh host COMMIT CACHEDIR STORAGE BRIDGE FORKURL...
#       On the Proxmox host: makes a throwaway Debian 13 container, builds in it, copies the result to CACHEDIR
#       (stela, stela.efi, BUILD-INFO, build.log) and destroys the container. Does nothing when CACHEDIR already
#       holds a build of COMMIT.
#   build-stela.sh inside COMMIT OUTDIR FORKURL...
#       In the container (the host mode runs this).
#
# The source is fetched by COMMIT alone from the first fork that serves it. A fork cannot hand over other code under
# that sha: git checks the object hash, and the script checks the commit it got is the commit it asked for.
set -euo pipefail

mode="${1:-}"
commit="${2:-}"
[[ "$mode" == host || "$mode" == inside ]] || { echo "usage: build-stela.sh host|inside COMMIT ..." >&2; exit 2; }
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "not a full commit sha: $commit" >&2; exit 2; }
step() { echo "== $(date -u +%H:%M:%S) $*"; }

# ---------------------------------------------------------------- inside the container
inside() {
  local out="$1"; shift
  [[ $# -gt 0 ]] || { echo "no fork URL given" >&2; exit 2; }
  export DEBIAN_FRONTEND=noninteractive CARGO_HOME=/opt/cargo RUSTUP_HOME=/opt/rustup
  export PATH="/opt/cargo/bin:/usr/lib/llvm-21/bin:$PATH"
  local llvm_key_fpr=6084F3CF814B57C1CF12EFD515CF4D18AF4F7421   # apt.llvm.org's public signing key fingerprint, not a secret  # gitleaks:allow
  local src=/opt/stela-src
  mkdir -p "$out"

  step "apt packages (Debian 13)"
  apt-get update -q
  apt-get install -y -q --no-install-recommends \
    ca-certificates curl gnupg git build-essential cmake ninja-build python3 python3-venv pkg-config \
    zstd xz-utils glslang-tools libssl-dev bison flex bc libelf-dev
  curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key -o /tmp/llvm.key
  local got
  got="$(gpg --show-keys --with-colons /tmp/llvm.key | awk -F: '/^fpr/{print $10; exit}')"
  [[ "$got" == "$llvm_key_fpr" ]] || { echo "the LLVM apt key is $got, not $llvm_key_fpr" >&2; exit 1; }
  gpg --dearmor < /tmp/llvm.key > /etc/apt/trusted.gpg.d/apt.llvm.org.gpg
  echo "deb http://apt.llvm.org/trixie/ llvm-toolchain-trixie-21 main" > /etc/apt/sources.list.d/llvm-21.list
  apt-get update -q
  apt-get install -y -q --no-install-recommends clang-21 lld-21 llvm-21 llvm-21-dev libclang-21-dev libclang-cpp21-dev

  step "source $commit"
  rm -rf "$src"
  git init -q "$src"
  local url fetched=""
  for url in "$@"; do
    if git -C "$src" fetch -q --depth 1 "$url" "$commit" 2>/dev/null; then
      got="$(git -C "$src" rev-parse "FETCH_HEAD^{commit}")"
      if [[ "$got" == "$commit" ]]; then fetched="$url"; break; fi
      echo "$url served $got, not $commit" >&2
    else
      echo "$url does not serve $commit" >&2
    fi
  done
  [[ -n "$fetched" ]] || { echo "no fork serves $commit" >&2; exit 1; }
  git -C "$src" checkout -q FETCH_HEAD
  echo "source from $fetched"

  step "Rust toolchain from the source's rust-toolchain.toml"
  if [[ ! -x /opt/cargo/bin/rustup ]]; then
    curl -fsSL https://sh.rustup.rs -o /tmp/rustup-init.sh
    sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain none --no-modify-path
  fi
  cd "$src"
  rustup toolchain install
  rustc --version
  clang --version | head -1

  step "SPIR-V libraries (the source's own pinned script)"
  bash .github/spirv.sh /opt/spirv
  export PKG_CONFIG_PATH=/opt/spirv/lib/pkgconfig

  # stela-soc-data overflows rustc's default compiler-thread stack (SIGSEGV); rustc itself suggests this size.
  export RUST_MIN_STACK=33554432
  step "cargo xtask dist: fetches Linux, Mesa, LLVM, libdrm and xnu at their pins, then builds"
  cargo xtask dist

  step "collect"
  install -m 0755 target/release/stela "$out/stela"
  local efi
  efi="$(find target -name stela-dist.efi | head -1)"
  [[ -n "$efi" ]] || { echo "the build left no stela-dist.efi" >&2; exit 1; }
  install -m 0644 "$efi" "$out/stela.efi"
  {
    echo "commit $commit"
    echo "fork $fetched"
    echo "rustc $(rustc --version)"
    echo "clang $(clang --version | head -1)"
    echo "os $(. /etc/os-release; echo "$PRETTY_NAME") $(cat /etc/debian_version)"
    echo "stela $("$out/stela" --version)"
    dpkg-query -W -f='deb ${Package} ${Version}\n' clang-21 llvm-21 lld-21 libclang-21-dev
    (cd "$out" && sha256sum stela stela.efi)
  } > "$out/BUILD-INFO"
  cat "$out/BUILD-INFO"
  step "built"
}

# ---------------------------------------------------------------- on the Proxmox host
host() {
  local cache="$1" storage="$2" bridge="$3"; shift 3
  [[ "$storage" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "bad storage name: $storage" >&2; exit 2; }
  [[ "$bridge" =~ ^vmbr[0-9]+$ ]] || { echo "bad bridge: $bridge" >&2; exit 2; }
  [[ $# -gt 0 ]] || { echo "no fork URL given" >&2; exit 2; }

  if [[ -x "$cache/stela" && -s "$cache/stela.efi" ]] && grep -qx "commit $commit" "$cache/BUILD-INFO" 2>/dev/null; then
    echo "Stela $commit is already built in $cache: using it"
    return 0
  fi
  mkdir -p "$cache"
  local log="$cache/build.log"
  echo "Building Stela $commit: a throwaway Debian 13 container, about one hour. Log: $log"

  local tmpl
  tmpl="$(pveam list local | awk '$1 ~ /debian-13-standard.*amd64/ { print $1 }' | sort -V | tail -1)"
  if [[ -z "$tmpl" ]]; then
    pveam update >/dev/null
    local name
    name="$(pveam available --section system | awk '$2 ~ /^debian-13-standard.*amd64/ { print $2 }' | sort -V | tail -1)"
    [[ -n "$name" ]] || { echo "no Debian 13 container template is available" >&2; exit 1; }
    pveam download local "$name"
    tmpl="local:vztmpl/$name"
  fi

  local ct mem
  ct="$(pvesh get /cluster/nextid | tr -d "\"[:space:]")"
  mem=$(awk '/MemTotal/ { m = int($2 / 1024 * 3 / 4); if (m > 40960) m = 40960; print m }' /proc/meminfo)
  # shellcheck disable=SC2064
  trap "pct stop $ct --skiplock >/dev/null 2>&1 || true; pct destroy $ct --purge --force >/dev/null 2>&1 || true" EXIT
  step "container $ct from $tmpl on $storage"
  pct create "$ct" "$tmpl" --hostname stela-build --unprivileged 1 --rootfs "$storage:90" --cores "$(nproc)" \
    --memory "$mem" --swap 0 --net0 "name=eth0,bridge=$bridge,ip=dhcp" --features nesting=1 --onboot 0 \
    --tags osx-next-stela-build
  pct start "$ct"
  local n
  for n in $(seq 1 60); do
    pct exec "$ct" -- getent hosts apt.llvm.org >/dev/null 2>&1 && break
    sleep 2
    [[ $n -lt 60 ]] || { echo "the container has no network on $bridge" >&2; exit 1; }
  done
  pct push "$ct" "${BASH_SOURCE[0]}" /root/build-stela.sh --perms 755
  pct exec "$ct" -- bash /root/build-stela.sh inside "$commit" /out "$@" 2>&1 | tee "$log"

  step "copy the build to $cache"
  local part="$cache/.new"
  rm -rf "$part"; mkdir -p "$part"
  local f
  for f in stela stela.efi BUILD-INFO; do pct pull "$ct" "/out/$f" "$part/$f"; done
  grep -qx "commit $commit" "$part/BUILD-INFO" || { echo "the build is not of $commit" >&2; exit 1; }
  chmod 755 "$part/stela"
  mv -f "$part/stela.efi" "$part/BUILD-INFO" "$cache/"
  mv -f "$part/stela" "$cache/stela"
  rmdir "$part"
  echo "Stela built and cached in $cache"
}

shift 2
if [[ "$mode" == host ]]; then host "$@"; else inside "$@"; fi
