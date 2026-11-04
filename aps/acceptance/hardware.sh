#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The suite's hardware use on the acceptance VM ($MACHINE), from the one command to the last check: the bootstrap's
# floors come from it (aps/bootstrap/install.sh).
#   start <dir>   writes <dir>/machine.txt (the cores and MemTotal) and starts a sampler on the machine, whose samples
#                 stream to <dir>/samples.txt as they are taken, so a wedged VM still leaves what was sampled. Once a
#                 second it notes the memory in use (MemTotal less MemAvailable: what the kernel cannot drop), the CPU
#                 time spent, the disk in use on /, and the kernel's running totals of the time tasks waited for memory
#                 or a core (pressure stall information) and of OOM kills. Not cgroup memory.peak: it counts page cache
#                 the kernel drops at will, so it grows with the image pulls and the VM's size, not the suite's need.
#   report <dir>  stops it, takes the machine's own copy of the samples if it can, and writes <dir>/peaks.txt: per stage
#                 (<dir>/stages.txt, which the caller writes, has "<epoch> <name>" as each began) and for the whole run,
#                 the peak memory in use, the busiest minute's cores and the peak disk in use above the start; and the
#                 stall seconds and OOM kills, which miss nothing between samples and so tell whether the VM's size held
#                 a peak down.
# Usage: hardware.sh start <dir> | hardware.sh report <dir>
set -euo pipefail
samples=/var/tmp/aps-hardware.txt

usage() { echo "usage: $0 start <dir> | $0 report <dir>" >&2; exit 2; }
[ "$#" -eq 2 ] || usage
dir=$2

case "$1" in
  start)
    mkdir -p "$dir"
    "$MACHINE" "nproc; awk '/^MemTotal:/ {print \$2}' /proc/meminfo" > "$dir/machine.txt"
    "$MACHINE" "cat > /var/tmp/aps-hardware.sh &&
      sudo systemd-run --quiet --unit aps-hardware bash /var/tmp/aps-hardware.sh $samples &&
      timeout 10 bash -c 'until [ -s $samples ]; do sleep 0.2; done'" << 'SAMPLER'
# A line a second: epoch; CPU time busy and not (USER_HZ); memory in use (kB); disk in use on / (MB); microseconds some
# task and every task waited for memory, and some task for a core; OOM kills; CPU time the host stole (USER_HZ, counted
# in not busy).
while :; do
  awk -v t="$(date +%s)" -v disk="$(df --output=used -B 1000000 / | tail -n 1)" '
    $1 == "cpu" { busy = $2 + $3 + $4 + $7 + $8; rest = $5 + $6 + $9; steal = $9 }
    $1 == "MemTotal:" { total = $2 }
    $1 == "MemAvailable:" { available = $2 }
    $1 == "some" || $1 == "full" { split($NF, value, "="); stalled[FILENAME " " $1] = value[2] }
    $1 == "oom_kill" { oom = $2 }
    END { printf "%.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f\n", t, busy, rest, total - available, disk,
            stalled["/proc/pressure/memory some"], stalled["/proc/pressure/memory full"],
            stalled["/proc/pressure/cpu some"], oom, steal }
  ' /proc/stat /proc/meminfo /proc/pressure/memory /proc/pressure/cpu /proc/vmstat
  sleep 1
done >> "$1"
SAMPLER
    "$MACHINE" "tail -n +1 -f $samples" < /dev/null > "$dir/samples.txt" 2> /dev/null &
    ;;
  report)
    if "$MACHINE" "sudo systemctl stop aps-hardware; cat $samples" > "$dir/samples.vm"; then
      mv "$dir/samples.vm" "$dir/samples.txt"
    else
      rm -f "$dir/samples.vm"
    fi
    { read -r cpus; read -r memory; } < "$dir/machine.txt"
    running="$("$MACHINE" "sudo docker ps --format '{{.Names}}' | sort | paste -s -d ' '" 2> /dev/null || true)"
    awk -v tag="$TAG" -v cpus="$cpus" -v memory="$memory" -v running="$running" '
      function row(label, gib, busiest, gb, some_us, full_us, waited_us, kills) {
        printf "%-20s %7.2f %6s %6.1f %7.1f/%-7.1f %9.1f %4d\n", label, gib, busiest, gb, some_us / 1e6, full_us / 1e6,
          waited_us / 1e6, kills
      }
      NR == FNR { began[++stages] = $1; name[stages] = $2; next }
      {
        n++; t[n] = $1; busy[n] = $2; rest[n] = $3
        while (s < stages && began[s + 1] <= $1) s++
        k = s ? s : 1
        if (n == 1) { start = $5; p6 = $6; p7 = $7; p8 = $8; p9 = $9 }
        if ($4 / 1048576 > mem[k]) mem[k] = $4 / 1048576
        if (($5 - start) / 1000 > disk[k]) disk[k] = ($5 - start) / 1000
        some[k] += $6 - p6; full[k] += $7 - p7; waited[k] += $8 - p8; oom[k] += $9 - p9
        p6 = $6; p7 = $7; p8 = $8; p9 = $9
        while (j < n && t[n] - t[j + 1] >= 60) j++
        if (j && t[n] - t[j] >= 60 && t[j] >= began[k]) {
          cores = cpus * (busy[n] - busy[j]) / (busy[n] - busy[j] + rest[n] - rest[j])
          if (!(k in cpu) || cores > cpu[k]) cpu[k] = cores
        }
      }
      END {
        printf "Hardware use on the acceptance VM, %s: %d cores, MemTotal %.2f GiB\n", tag, cpus, memory / 1048576
        printf "Running at the end: %s\n", running
        print "One sample a second from the one command to the last check (samples.txt; stages.txt times the stages)."
        print "memory: peak in use (MemTotal less MemAvailable), GiB. cpu: the busiest minute, cores. disk: peak in"
        print "use on / above the start, GB. Kernel totals, which miss nothing between samples: seconds some/every task"
        print "waited for memory, seconds some task waited for a core, OOM kills."
        print ""
        printf "%-20s %7s %6s %6s %15s %9s %4s\n", "stage", "memory", "cpu", "disk", "memory-stall", "cpu-stall", "oom"
        for (i = 1; i <= stages; i++) {
          row(name[i], mem[i], (i in cpu) ? sprintf("%.2f", cpu[i]) : "-", disk[i], some[i], full[i], waited[i], oom[i])
          if (mem[i] > all_mem) all_mem = mem[i]
          if ((i in cpu) && cpu[i] > all_cpu) all_cpu = cpu[i]
          if (disk[i] > all_disk) all_disk = disk[i]
          all_some += some[i]; all_full += full[i]; all_waited += waited[i]; all_oom += oom[i]
        }
        row("whole-run", all_mem, sprintf("%.2f", all_cpu), all_disk, all_some, all_full, all_waited, all_oom)
      }' "$dir/stages.txt" "$dir/samples.txt" > "$dir/peaks.txt"
    cat "$dir/peaks.txt"
    ;;
  *) usage ;;
esac
