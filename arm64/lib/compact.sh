#!/bin/sh
# compact.sh PACK COMPACTOR: run qemu-pcache-compact on PACK at idle priority and log the outcome (stdout, the caller appends to compact.log).
# R19: the compactor never waits for the pack lock (rc 2 = "in use"), and it is started the moment the CLI stops waiting for QEMU. After a
# resume that is too early: Ctrl-C or SIGTERM runs the CLI's trap, which interrupts `wait` while QEMU is still shutting down and holds the
# lock (5 of 5 resumes in R18 logged rc=2). So rc 2 is retried once a second for up to PCCOMPACTWAIT seconds (default 30, 0 = no retry).
# Only the last try is printed, with the time waited.
pack=$1; tool=$2; max=${PCCOMPACTWAIT:-30}
echo "$(date -Is) compact start: $pack"
i=0
while :; do
  out=$(nice -n 19 ionice -c3 timeout 1800 "$tool" "$pack" 2>&1); rc=$?
  [ "$rc" = 2 ] && [ "$i" -lt "$max" ] || break
  i=$((i + 1)); sleep 1
done
[ -n "$out" ] && echo "$out"
# R24 (S8 bug 4): the exit codes of qemu-pcache-compact are 0 compacted, 1 nothing to do (below the trigger: not an error), 2 pack in use,
# 3 error; the end line says which in words, so "rc=1" is not read as a failure
case $rc in
  0) what="compacted" ;;
  1) what="nothing to do, not an error" ;;
  2) what="pack in use, gave up" ;;
  *) what="FAILED" ;;
esac
echo "$(date -Is) compact end: $what (rc=$rc) waited=${i}s"
exit "$rc"
