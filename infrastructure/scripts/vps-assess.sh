#!/usr/bin/env bash
# READ-ONLY host suitability assessment for running Abu AI on an existing VPS.
#
#   bash infrastructure/scripts/vps-assess.sh > vps-assessment.txt
#   sudo bash infrastructure/scripts/vps-assess.sh > vps-assessment.txt     # a little more detail (firewall, LUKS)
#
# Guarantees (each is asserted by tests/unit/test_vps_assess.py):
#   * it changes nothing: no package install, no service start/stop, no file written outside stdout, no firewall change,
#     no `docker run/exec/stop/rm`, no network connection is opened;
#   * it never reads .env files, secrets/, key files, shell history, SSH keys or any user data; it does not run
#     `docker inspect` (which would print container environment variables) or `docker logs`;
#   * the whole output passes through a redaction filter: IPv4, IPv6, MAC addresses and the host name are replaced
#     before anything reaches stdout. Container NAMES are not printed, only images, status and published-port numbers.
# Review the output before sharing it. It is safe to paste into the owner/agent conversation after that review.
set -u
export LC_ALL=C

HOST_NAME="$(hostname 2>/dev/null || echo unknown)"
HOST_FQDN="$(hostname -f 2>/dev/null || echo "$HOST_NAME")"

redact() {
  sed -E \
    -e "s/\\b${HOST_FQDN//./\\.}\\b/<host>/g" \
    -e "s/\\b${HOST_NAME//./\\.}\\b/<host>/g" \
    -e 's/\b([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b/<mac>/g' \
    -e 's/\b([0-9]{1,3}\.){3}[0-9]{1,3}(\/[0-9]{1,2})?\b/<ipv4>/g' \
    -e 's/[0-9a-fA-F:]*::[0-9a-fA-F:]*(\/[0-9]{1,3})?/<ipv6>/g' \
    -e 's/\b([0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}(\/[0-9]{1,3})?\b/<ipv6>/g'
}

have() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n=== %s ===\n' "$1"; }
IS_ROOT=0; [ "$(id -u 2>/dev/null)" = "0" ] && IS_ROOT=1

main() {
  echo "abu-ai vps-assess v1 (read-only). run as root: $([ $IS_ROOT = 1 ] && echo yes || echo no)"
  date -u +"utc: %Y%m%dT%H%M%SZ"

  section "OS and kernel"
  if [ -r /etc/os-release ]; then grep -E '^(PRETTY_NAME|VERSION_ID|ID)=' /etc/os-release; fi
  uname -sr 2>/dev/null
  uname -m 2>/dev/null
  if have systemd-detect-virt; then echo "virtualisation: $(systemd-detect-virt 2>/dev/null)"; fi

  section "CPU, memory, swap, load"
  echo "cpus: $(nproc 2>/dev/null)"
  grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/^[^:]*: */cpu model: /'
  grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo 2>/dev/null
  echo "loadavg(1/5/15): $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
  uptime -p 2>/dev/null

  section "Disk capacity (mount points and usage only)"
  df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null
  echo "-- inodes"
  df -i -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | head -12

  section "Disk encryption evidence (G1 input; NOT proof by itself)"
  if have lsblk; then lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT 2>/dev/null; else echo "lsblk not available"; fi
  if have dmsetup; then
    echo "-- dm-crypt targets (needs root to list reliably)"
    dmsetup ls --target crypt 2>&1 | head -10
  fi
  if have findmnt; then
    for d in /var/lib/docker /var/lib/postgresql /srv /opt /home; do
      [ -e "$d" ] && echo "backing device for $d: $(findmnt -no SOURCE -T "$d" 2>/dev/null) (fstype $(findmnt -no FSTYPE -T "$d" 2>/dev/null))"
    done
  fi
  if [ -r /proc/crypto ]; then echo "kernel crypto: aes=$(grep -c '^name.*: *aes' /proc/crypto) xts=$(grep -c '^name.*: *xts' /proc/crypto)"; fi
  echo "note: a crypt/LUKS device under the Docker data root, or provider-side volume encryption confirmed in the provider"
  echo "      console, is needed for gate G1. A guest VM cannot see host-level encryption; confirm that with the provider."

  section "Docker and Compose"
  if have docker; then
    docker --version 2>&1
    docker compose version 2>&1 | head -1
    echo "-- daemon (does not start anything)"
    docker info --format 'server={{.ServerVersion}} storage={{.Driver}} root-dir-fs={{.DockerRootDir}} cgroup={{.CgroupVersion}} rootless={{range .SecurityOptions}}{{.}} {{end}}' 2>&1 | head -2
    echo "-- containers: image, status, published ports (names and environments deliberately NOT shown)"
    docker ps -a --format '{{.Image}} | {{.Status}} | {{.Ports}}' 2>&1 | head -40
    echo "-- count: $(docker ps -aq 2>/dev/null | wc -l) containers, $(docker ps -q 2>/dev/null | wc -l) running"
    echo "-- volumes: $(docker volume ls -q 2>/dev/null | wc -l)   networks: $(docker network ls -q 2>/dev/null | wc -l)"
    docker system df 2>&1 | head -6
  else
    echo "docker: not installed"
  fi

  section "Listening sockets (address classified, process name only)"
  if have ss; then
    ss -H -tlnp 2>/dev/null | awk '
      { split($4, a, ":"); port=a[length(a)]; addr=$4; sub(":" port "$", "", addr);
        cls = (addr ~ /^(127\.|\[::1\]|::1)/) ? "loopback" : ((addr == "*" || addr == "0.0.0.0" || addr == "[::]" || addr == "::") ? "ALL-INTERFACES" : "specific-address");
        proc=$NF; sub(/^users:\(\(\"/, "", proc); sub(/\".*/, "", proc);
        printf "tcp/%s  %s  %s\n", port, cls, (proc ~ /users:/ ? "-" : proc) }' | sort -t/ -k2 -n | uniq
    echo "-- udp (count only)"; ss -H -ulnp 2>/dev/null | wc -l
  elif have netstat; then
    netstat -tln 2>/dev/null | awk 'NR>2 {split($4, a, ":"); port=a[length(a)]; addr=$4; sub(":" port "$", "", addr);
      cls = (addr ~ /^(127\.|::1)/) ? "loopback" : ((addr == "0.0.0.0" || addr == "::" || addr == ":::") ? "ALL-INTERFACES" : "specific-address");
      printf "tcp/%s  %s  -\n", port, cls}' | sort -t/ -k2 -n | uniq
  else
    echo "neither ss nor netstat is available: LISTENING PORTS NOT ASSESSED"
  fi

  section "Firewall (read-only inspection; needs root for detail)"
  if have ufw; then ufw status verbose 2>&1 | head -25; else echo "ufw: not installed"; fi
  if have nft; then echo "nft rules: $(nft list ruleset 2>/dev/null | grep -c . ) lines (needs root; 0 can mean no permission)"; fi
  if have iptables; then echo "iptables policies:"; iptables -S 2>/dev/null | grep -E '^-P' ; fi
  if have firewall-cmd; then firewall-cmd --state 2>&1 | head -1; fi
  echo "note: provider-level (cloud) firewalls are not visible from inside the VM. State them in the owner reply."

  section "Existing web server or reverse proxy"
  for p in caddy nginx apache2 httpd traefik haproxy envoy; do
    if pgrep -x "$p" >/dev/null 2>&1; then echo "running: $p"; fi
  done
  have systemctl && systemctl list-units --type=service --state=running --no-legend 2>/dev/null \
    | awk '{print $1}' | grep -Ei 'caddy|nginx|apache|httpd|traefik|haproxy|docker|containerd|postgres|redis|mysql|mariadb|mongo|fail2ban|unattended|cron|chrony|ntp|ssh' | head -30
  echo "-- ports 80/443 owner"
  ss -H -tlnp 2>/dev/null | awk '$4 ~ /:(80|443)$/ {print $4, $NF}' | sed -E 's/^[^ ]*:(80|443) /:\1 /' | head -6

  section "Hardening signals"
  have systemctl && echo "time-sync: $(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
  have systemctl && echo "unattended-upgrades active: $(systemctl is-active unattended-upgrades 2>/dev/null)"
  have systemctl && echo "fail2ban active: $(systemctl is-active fail2ban 2>/dev/null)"
  # Effective SSH policy as keywords only (no keys). Needs root; absent otherwise.
  if [ $IS_ROOT = 1 ] && have sshd; then sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication|pubkeyauthentication) '; else echo "sshd policy: needs root (not shown)"; fi

  section "Backup destinations (capacity only; nothing is listed or read)"
  for d in /mnt /media /backup /backups /srv; do
    [ -d "$d" ] && echo "$d: $(df -hP "$d" 2>/dev/null | awk 'NR==2{print $2" total, "$4" free, mount "$6}')"
  done
  echo "note: an off-host destination cannot be detected from here. State the intended one in the owner reply."

  section "Presence of Abu AI related files (names and permissions only, never contents)"
  for f in .env secrets/memory-keyring.json; do
    for root in "$PWD" "$PWD/.." ; do
      if [ -e "$root/$f" ]; then stat -c "%n mode=%a owner=%U" "$root/$f" 2>/dev/null | sed "s#$root/##"; fi
    done
  done

  section "Verdict inputs (the assessor applies the thresholds in docs/operations/VPS_ASSESSMENT.md)"
  mem_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)
  disk_kb=$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')
  echo "mem_available_mib=$(( ${mem_kb:-0} / 1024 ))"
  echo "root_free_gib=$(( ${disk_kb:-0} / 1048576 ))"
  echo "cpus=$(nproc 2>/dev/null)"
  echo "ports_80_443_in_use=$(ss -H -tln 2>/dev/null | awk '$4 ~ /:(80|443)$/' | wc -l)"
  echo "end of report"
}

main 2>&1 | redact
