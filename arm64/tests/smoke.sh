#!/usr/bin/env bash
# Offline smoke tests: no Apple files, no QEMU build needed. Run from anywhere.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
S=$HERE/snapintosh
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export SNAPINTOSH_HOME=$T/home XDG_CONFIG_HOME=$T/cfg
fail=0
t() { local name=$1; shift; if "$@" >"$T/out" 2>&1; then echo "pass  $name"; else echo "FAIL  $name"; sed 's/^/      /' "$T/out" | head -20; fail=1; fi; }

t shellcheck shellcheck -x "$S" "$HERE/tests/smoke.sh"
t shellcheck-guest shellcheck -s sh "$HERE/lib/guest/apply-sshd-interactive.sh"
t vkparse-radv bash -c "python3 -I '$HERE/lib/vkparse.py' VK_EXT_fragment_shader_interlock VK_EXT_external_memory_host < '$HERE/tests/fixtures/vulkaninfo-radv.txt' | grep -q '0x1002	\$'"
t vkparse-anv bash -c "python3 -I '$HERE/lib/vkparse.py' VK_EXT_fragment_shader_interlock VK_EXT_external_memory_host < '$HERE/tests/fixtures/vulkaninfo-anv.txt' | grep -q '0x8086	\$'"
t vkparse-missing bash -c "python3 -I '$HERE/lib/vkparse.py' VK_EXT_not_a_real_ext < '$HERE/tests/fixtures/vulkaninfo-radv.txt' | grep -q 'VK_EXT_not_a_real_ext'"
t help "$S" --help
# R29: `python3 -m unittest` finds every tests/test_*.py (as one case each), and only lib/guestapp.py builds the command that opens an app
t unittest-discovers-every-script bash -c "cd '$HERE' && python3 -c \"import glob, unittest; n = unittest.defaultTestLoader.discover('.').countTestCases(); w = len(glob.glob('tests/test_*.py')); assert n == w and n > 10, (n, w)\""
t one-open-helper bash -c "cd '$HERE' && ! grep -n 'open -a' lib/benchctl.py lib/goldenctl.py lib/snapctl.py snapintosh | grep -v '^.*#' && grep -q 'open -a' lib/guestapp.py"
t app-opts-rejected bash -c "! '$S' snapshot x --open 'http://a/b;id' 2>&1 | grep -q 'unknown option'; ! '$S' snapshot x --open 'http://a/b;id' >/dev/null 2>&1; ! '$S' run --app Safari >/dev/null 2>&1"
t mac-steps bash -c "'$S' make-disk mac-steps | grep -q 'vzinstall install UniversalMac_27.0_26A428_Restore.ipsw'"
t mac-steps-no-avp bash -c "'$S' make-disk mac-steps | grep -q 'No AVPBooter is needed'"
t mac-steps-26b131 bash -c "'$S' make-disk mac-steps --build 26B131 | grep -q 'UniversalMac_27.2.1_26B131_Restore.ipsw'"
t fetch-dry bash -c "SNAPINTOSH_DRY=1 '$S' fetch | grep -q 'UniversalMac_27.0_26A428'"
t fetch-dry-26b131 bash -c "SNAPINTOSH_DRY=1 '$S' fetch --build 26B131 2>&1 | grep -q 'UniversalMac_27.2.1_26B131'"
t fetch-bad-build bash -c "! SNAPINTOSH_DRY=1 '$S' fetch --build 26X999"
t pins-sha bash -c ". '$HERE/pins.env'; [ \${#IPSW_26A428_SHA256} -eq 64 ] && [ \${#IPSW_26B131_SHA256} -eq 64 ] && [ \${#QEMU_REF} -eq 40 ] && [ \${#AMBROSIA_REF} -eq 40 ] && [ -z \"\$(echo \$QEMU_REF | tr -d 0-9a-f)\" ]"
t pins-release-v2 bash -c "grep -q 'QEMU_REF=\${QEMU_REF:-8df7ee62c81bc602b0eec0cd23c1fa11994a7094}' '$HERE/pins.env' && [ \$(ls '$HERE'/patches/qemu/*.patch | wc -l) -eq 8 ] && grep -q c36556f3118e04fca90005ae0f0dfd68 '$HERE/pins.env' && grep -q 'P102_BQLSPIN' '$HERE/patches/qemu/0008'*.patch && grep -q 'P102_BQLSPIN' '$HERE/README.md'"
t pins-patch-head bash -c ". '$HERE/pins.env'; head -1 '$HERE'/patches/qemu/0008-*.patch | grep -q \"^From \$QEMU_REF \""
t pins-patch-series bash -c "n=0; for f in '$HERE'/patches/qemu/*.patch; do n=\$((n+1)); case \$(basename \$f) in \$(printf %04d \$n)-*) ;; *) exit 1 ;; esac; done; [ \$n -eq 8 ]"
t ambrosia-patches bash -c "[ \$(ls '$HERE'/patches/ambrosia/*.patch | wc -l) -eq 3 ]"
t build-deps bash -c "'$S' build --deps | grep -q libslirp-dev"
t build-deps-cross bash -c "'$S' build --deps | grep -q aarch64-unknown-none"
t sshd-interactive-note bash -c "'$S' make-disk sshd-interactive | grep -q ProcessType; sh -n '$HERE/lib/guest/apply-sshd-interactive.sh'"
t quiet-note bash -c "'$S' make-disk quiet | grep -q apply-quiet.sh"
t build-needs-repo bash -c "! '$S' build --only qemu 2>&1 | grep -q 'unexpected'; '$S' build --only reims 2>&1 | grep -q 'REIMS_REPO\|M2V_REPO'"

# import: synthetic bundle (random bytes, a real machineid plist)
B=$T/bundle; mkdir -p "$B"
head -c 1048576 /dev/zero > "$B/disk.img"
head -c 33554432 /dev/zero > "$B/aux.img"
python3 -I -c "import plistlib,sys;plistlib.dump({'ECID': 4533301000242790675}, open(sys.argv[1],'wb'))" "$B/machineid.bin"
echo hw > "$B/hardwaremodel.bin"
t import "$S" make-disk import "$B"
t import-no-avp bash -c "[ ! -e '$T/home/vm/AVPBooter.vmapple2.bin' ]"
t import-aux-trim bash -c "[ \$(stat -c %s '$T/home/vm/aux.img') -eq \$((33554432 - 16384)) ]"
echo boot > "$T/avp.bin"
t import-avp-optional bash -c "'$S' make-disk import '$B' --avpbooter '$T/avp.bin' && [ -f '$T/home/vm/AVPBooter.vmapple2.bin' ]"
t import-avp-missing-file bash -c "! '$S' make-disk import '$B' --avpbooter '$T/nope.bin'"

# run --dry-run prints the D3 defaults (Ambrosia boot)
mkdir -p "$T/home/vm/golden"; : > "$T/home/vm/golden/root-sshd.qcow2"
t dry-run bash -c "'$S' run --gpu 0 --dry-run"
"$S" run --gpu 0 --dry-run > "$T/dry" 2>&1
for needle in 'fw=ambrosia' 'tb-size=2047' 'pcache-mode=rw' 'ambrosia=on' 'ambrosia.bin' 'addr=0x120000000' 'force-raw=on' 'config.cpio' \
  'SNAP_IOFENCE_PATCH=1' 'align=2M' 'M2V_INPLACE_RT=1' 'LP_NUM_THREADS=8' 'cpu max' 'smp 12' 'romfile='; do
  t "dry-run has $needle" grep -q -- "$needle" "$T/dry"
done
for needle in 'csr-config' 'xnu-boot-args' 'internal-use-only-unit' 'AVPBooter'; do
  t "dry-run (ambrosia) lacks $needle" bash -c "! grep -q -- '$needle' '$T/dry'"
done
t dry-run-cpus bash -c "'$S' run --gpu 0 --cpus 6 --dry-run | grep -q 'smp 6'"
t dry-run-iofence-off bash -c "SNAP_IOFENCE_PATCH=0 '$S' run --gpu 0 --dry-run | grep -q 'SNAP_IOFENCE_PATCH=0'"
# legacy AVPBooter path keeps the old lab flags
"$S" run --gpu 0 --fw avp --dry-run > "$T/dry-avp" 2>&1
for needle in 'fw=avp' 'csr-config=0x7f' 'xnu-boot-args=-v' 'internal-use-only-unit=on' 'AVPBooter.vmapple2.bin'; do
  t "dry-run avp has $needle" grep -q -- "$needle" "$T/dry-avp"
done
t dry-run-bad-fw bash -c "! '$S' run --fw nope --dry-run"
t dry-run-gpu bash -c "touch '$T/icd.json'; '$S' run --gpu 1 --icd '$T/icd.json' --dry-run | grep -q 'ICD=$T/icd.json'"
t dry-run-resume bash -c "'$S' resume foo --gpu 0 --dry-run | grep -q 'incoming'"
t adopt-golden bash -c "H=$T/ag; mkdir -p \$H/vm \$H/ssh; export SNAPINTOSH_HOME=\$H; truncate -s 8M \$H/vm/disk.img; touch \$H/ssh/id_ed25519; \
  qemu-img create -q -f qcow2 -b \$H/vm/disk.img -F raw \$H/o1.qcow2; qemu-img create -q -f qcow2 -b \$H/o1.qcow2 -F qcow2 \$H/o2.qcow2; \
  '$S' make-disk adopt-golden \$H/o2.qcow2 --ssh-state \$H/ssh | grep -q 'golden adopted'; \
  qemu-img info -U --backing-chain \$H/vm/golden/root-quiet.qcow2 | grep -q \"backing file: \$H/vm/disk.img\"; \
  [ \$(ls \$H/vm/golden/chain | wc -l) -eq 2 ] && [ -f \$H/vm/golden/ssh-state/id_ed25519 ]"
t adopt-golden-needs-chain bash -c "H=$T/ag2; mkdir -p \$H/vm; export SNAPINTOSH_HOME=\$H; truncate -s 8M \$H/vm/disk.img; ! '$S' make-disk adopt-golden \$H/vm/disk.img 2>&1 | grep -q unexpected; ! '$S' make-disk adopt-golden \$H/vm/disk.img"
t shellcheck-golden-guest shellcheck -s sh "$HERE/lib/guest/golden-phase1.sh"
t golden-dry bash -c "SNAPINTOSH_DRY=1 '$S' make-disk golden '$T/home/vm/disk.img' --gpu 0 | grep -q 'phase 1, offline'"
t golden-missing-base bash -c "! '$S' make-disk golden '$T/nope.img' 2>&1 | grep -q unexpected; ! '$S' make-disk golden '$T/nope.img'"
t golden-bad-option bash -c "! '$S' make-disk golden --bogus"
t kcpassword bash -c "python3 -I -c \"
import importlib.util
s = importlib.util.spec_from_file_location('g', '$HERE/lib/goldenctl.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
k = m.kcpassword('snaplab'); assert len(k) == 12 and len(m.kcpassword('abcdefghijkl')) == 24
key = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
assert bytes(c ^ key[i % 11] for i, c in enumerate(k)).rstrip(b'\\0') == b'snaplab'
\""
t sshwait-unit python3 -I "$HERE/tests/test_sshwait.py"
t wallpaper-unit python3 -I "$HERE/tests/test_wallpaper.py"
t compact-unit python3 -I "$HERE/tests/test_compact.py"
t hygiene python3 -I "$HERE/tests/test_hygiene.py"
t rc-lint python3 -I "$HERE/tests/test_rc.py"
t ssh-hostkey python3 -I "$HERE/tests/test_ssh.py"
t snapctl-names-atomic-quoting python3 -I "$HERE/tests/test_snapctl.py"
t goldenctl-user-password python3 -I "$HERE/tests/test_goldenctl.py"
t golden-shutdown-wait python3 -I "$HERE/tests/test_golden_shutdown.py"
t golden-phase1-overlay-kept bash -c "f=\$(mktemp); sed -n '/^CL_PIDS=()/,/^cleanup_reset()/p' '$S' > \$f.fn; . \$f.fn; CL_P1=\$f.qcow2; : > \$CL_P1; cleanup_reset; [ -f \$f.qcow2 ] && [ ! -f \$f.failed.qcow2 ]; r=\$?; rm -f \$f* ; exit \$r"
t fetch-verify python3 -I "$HERE/tests/test_fetch.py"
t launch-rc-signals-save-resume-cleanup python3 -I "$HERE/tests/test_launch.py"
t golden-password-argv-gone bash -c "! '$S' make-disk golden --password x >/dev/null 2>&1 && '$S' make-disk golden --password x 2>&1 | grep -q 'is gone'"
t golden-user-validated bash -c "! '$S' make-disk golden --user 'a;b' >/dev/null 2>&1 && '$S' make-disk golden --user 'a;b' 2>&1 | grep -q 'short name'"
t snapshot-name-dotdot bash -c "! '$S' snapshot .. --dry-run >/dev/null 2>&1; [ -d '$T/home' ]"
t vnc-dry-password bash -c "'$S' run --gpu 0 --vnc --dry-run | grep -q 'password-secret=vncsec'"
t vnc-dry-insecure bash -c "! '$S' run --gpu 0 --vnc --vnc-insecure --dry-run | grep -q 'password-secret'"
t home-owner-only bash -c "SNAPINTOSH_HOME='$T/own' SNAPINTOSH_DRY=1 '$S' fetch --out '$T/own/dl' >/dev/null 2>&1; [ \$(stat -c %a '$T/own') = 700 ]"
t fetch-dir-shareable bash -c "SNAPINTOSH_HOME='$T/own2' SNAPINTOSH_DRY=1 '$S' fetch --out '$T/shared-dl' >/dev/null 2>&1; [ \$(stat -c %a '$T/own2') = 700 ] && [ \$(stat -c %a '$T/shared-dl') = 755 ]"
t snapshot-name-too-long bash -c "n=\$(printf 'n%.0s' \$(seq 60)); SNAPINTOSH_DRY=1 '$S' snapshot \$n --gpu 0 2>&1 | grep -q 'too long'"
t bench-unit python3 -I "$HERE/tests/test_bench.py"
t hostgaps-r25 python3 -I "$HERE/tests/test_hostgaps.py"
t readme-says-a-user-process-naming-a-run-dir-is-stopped bash -c "grep -q 'tail -F. on its .qemu.out.*stopped when the run ends' '$HERE/README.md'"
t bench-help bash -c "'$S' bench --help | grep -q -- '--runs'"
t bench-dry bash -c "'$S' bench --dry-run --runs 2 | grep -q 'snapintosh bench (dry run): 2 run(s)'"
t bench-ipsw-build bash -c "'$S' bench --dry-run --runs 1 | grep -q '\"ipsw_build\": \"26A428\"'"
t bench-bad-runs bash -c "! '$S' bench --runs 0 --dry-run"
t readme-numbers-match-the-record python3 -I "$HERE/tests/test_docs.py"
t bench-compile python3 -I -c "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], \"exec\")" "$HERE/lib/benchctl.py"
exit $fail
