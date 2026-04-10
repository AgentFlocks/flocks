#!/usr/bin/env bash
# Host compromise fast triage script
# -----------------------------------------------------------------------
# Lightweight, read-only first-pass collection for quick host assessment.
# Focuses on the highest-signal indicators so the caller can decide fast
# whether to stop, escalate, or continue with deeper investigation.
# -----------------------------------------------------------------------

LANG=C
export LANG

_s() { printf '\n### %s ###\n' "$1"; }

_s "TRIAGE_FAST_START"
date -u
hostname
uname -a
uptime

_s "CPU_TOP_PROCESSES"
ps aux --sort=-%cpu 2>/dev/null | head -15 || ps aux 2>/dev/null | head -15

_s "KNOWN_MINER_PROCESSES"
ps aux 2>/dev/null | grep -iE 'xmrig|minerd|cpuminer|cgminer|bfgminer|ethminer|nbminer|phoenixminer|t-rex|gminer|kinsing' | grep -v grep

_s "NETWORK_ESTABLISHED"
ss -tunap 2>/dev/null | grep -v "127\.0\.0\.1\|::1" | grep ESTAB | head -25 || \
  netstat -tunap 2>/dev/null | grep -v "127\.0\.0\.1\|::1" | grep ESTABLISHED | head -25

_s "SUSPICIOUS_NETWORK_TO_KNOWN_PORTS"
ss -tunap 2>/dev/null | grep -E ':3333|:4444|:5555|:14444|:45700|:8899|:9999' | grep ESTAB | head -15

_s "LISTENING_PORTS"
ss -tlnup 2>/dev/null | head -20 || netstat -tlnup 2>/dev/null | head -20

_s "TMP_EXECUTABLES"
find /tmp /dev/shm /var/tmp -type f -executable 2>/dev/null | head -20

_s "CRON_JOBS"
crontab -l 2>/dev/null
echo '---'
cat /etc/crontab 2>/dev/null
echo '---'
cat /etc/cron.d/* 2>/dev/null | head -40

_s "SYSTEMD_RUNNING_SERVICES"
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -25

_s "SSH_AUTHORIZED_KEYS_ROOT"
cat /root/.ssh/authorized_keys 2>/dev/null

_s "LD_SO_PRELOAD"
cat /etc/ld.so.preload 2>/dev/null

_s "OPEN_FILES_DELETED"
lsof 2>/dev/null | grep '(deleted)' | head -15

_s "RECENT_AUTH_EVENTS"
grep -E 'Failed password|Accepted password|Accepted publickey|Invalid user|ROOT' \
  /var/log/auth.log 2>/dev/null | tail -50 || \
grep -E 'Failed password|Accepted password|Accepted publickey|Invalid user|ROOT' \
  /var/log/secure 2>/dev/null | tail -50

_s "RECENTLY_MODIFIED_FILES"
find /root /home /tmp /var/tmp /dev/shm /etc /usr/local /opt -maxdepth 3 -type f -mtime -3 2>/dev/null | head -40

_s "TRIAGE_FAST_COMPLETE"
date -u
