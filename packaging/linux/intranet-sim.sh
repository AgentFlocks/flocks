#!/usr/bin/env bash
#
# Simulate a customer intranet for testing flocks-offline.run in Docker: the server under test
# runs in the network namespace of a small gateway container (`--network container:<gateway>`)
# whose egress firewall lets through only the endpoints the offline install manual tells
# customers to open for the Flocks server (intranet-allowlist.txt), and whose DNS answers only
# those names. Everything else is unreachable: other names do not resolve, other addresses and
# ports are dropped (timeouts, as behind a real firewall). Ports published on the gateway still
# reach the server, so a browser on the test host can use the WebUI.
#
# Usage:
#   intranet-sim.sh up    --name GATEWAY [--allowlist FILE] [--publish HOSTPORT:PORT ...]
#   intranet-sim.sh check CONTAINER [--allowlist FILE]
#   intranet-sim.sh down  --name GATEWAY
#   intranet-sim.sh render [--allowlist FILE]
#   intranet-sim.sh render-check [--allowlist FILE]
#   intranet-sim.sh verify-rules --family 4|6 [--allowlist FILE] < (iptables|ip6tables) -S OUTPUT
#
#   up      starts the gateway (alpine + iptables + dnsmasq; needs internet once for apk), applies
#           the egress rules and points DNS at the gateway's dnsmasq. Then start the server with
#           `docker run --network container:GATEWAY ...` (publish ports on the gateway, not on the
#           server). The server keeps the namespace across `docker restart`.
#   check   runs inside CONTAINER (the server, or anything sharing the namespace; needs bash,
#           curl, getent, timeout): every allowlisted endpoint answers, public names do not
#           resolve, other addresses, other ports and UDP DNS to public resolvers get nothing;
#           then reads the gateway's IPv4 / IPv6 OUTPUT rules (policy DROP, nothing accepted
#           beyond loopback, replies and the allowlist). PASS/FAIL per line, exit 1 when anything
#           is off.
#   down    removes the gateway (remove the containers sharing its namespace first).
#   render  prints the shell script `up` runs inside the gateway (rules + DNS), for review;
#           render-check prints the probe script `check` runs inside the container;
#           verify-rules is the rule check `check` applies to the gateway's OUTPUT chains.
#
# Environment: DOCKER (default docker), INTRANET_GW_IMAGE (default alpine:3.20).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${DOCKER:-docker}"
GW_IMAGE="${INTRANET_GW_IMAGE:-alpine:3.20}"
ALLOWLIST="$SCRIPT_DIR/intranet-allowlist.txt"
LABEL="flocks.intranet-sim=1"
NAME=""
TARGET=""
PUBLISH=()
FAMILY=""
ENTRIES=""

info() { printf '[intranet-sim] %s\n' "$*"; }
die() { printf '[intranet-sim] error: %s\n' "$*" >&2; exit 2; }

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

valid_ipv4() {
  local ip="$1" octet
  local -a octets
  [[ "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a octets <<< "$ip"
  for octet in "${octets[@]}"; do
    (( 10#$octet <= 255 )) || return 1
  done
}

parse_allowlist() {
  # one "<domain> <ip> <port>" line per address; any malformed line stops everything
  local file="$1" lineno=0 line domain ips port extra ip
  local -a list
  [[ -f "$file" ]] || die "allowlist not found: $file"
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    line="${line%%#*}"
    domain="" ips="" port="" extra=""
    read -r domain ips port extra <<< "$line" || true
    if [[ -z "$domain" ]]; then continue; fi
    if [[ -z "$port" || -n "$extra" ]]; then die "$file:$lineno: expected '<domain> <ipv4>[,<ipv4>...] <tcp port>'"; fi
    [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] \
      || die "$file:$lineno: not a domain name: $domain"
    [[ "$port" =~ ^[0-9]+$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || die "$file:$lineno: not a TCP port: $port"
    IFS=, read -r -a list <<< "$ips"
    [[ "${#list[@]}" -gt 0 ]] || die "$file:$lineno: no address for $domain"
    for ip in "${list[@]}"; do
      valid_ipv4 "$ip" || die "$file:$lineno: not an IPv4 address: $ip"
      printf '%s %s %s\n' "$domain" "$ip" "$((10#$port))"
    done
  done < "$file"
}

load_entries() {
  # parsed once, up front and outside any `cmd || ...` context: inside such a command bash
  # suspends `set -e`, so a malformed line found there would be skipped instead of stopping
  ENTRIES="$(parse_allowlist "$ALLOWLIST")" || exit 2
  [[ -n "$ENTRIES" ]] || die "$ALLOWLIST allows nothing"
}

render() {
  # the script `up` pipes into the gateway (POSIX sh, busybox); prints nothing else
  local domain ip port
  printf 'set -eu\n'
  printf '# DNS: only the allowlisted names resolve (to the manual'"'"'s addresses), everything else is NXDOMAIN\n'
  printf "cat > /etc/flocks-intranet-dnsmasq.conf <<'EOF'\n"
  printf 'no-resolv\nno-hosts\nlisten-address=127.0.0.1\nbind-interfaces\n'
  while read -r domain ip port; do
    printf 'host-record=%s,%s\n' "$domain" "$ip"
  done <<< "$ENTRIES"
  printf 'address=/#/\nEOF\n'
  printf 'dnsmasq --conf-file=/etc/flocks-intranet-dnsmasq.conf\n'
  printf "printf 'nameserver 127.0.0.1\\\\n' > /etc/resolv.conf\n"
  printf '# egress: loopback, replies to inbound connections (published ports), the allowlist; drop the rest\n'
  printf 'iptables -P OUTPUT ACCEPT\niptables -F OUTPUT\n'
  printf 'iptables -A OUTPUT -o lo -j ACCEPT\n'
  printf 'iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n'
  while read -r domain ip port; do
    printf 'iptables -A OUTPUT -p tcp -d %s --dport %s -j ACCEPT  # %s\n' "$ip" "$port" "$domain"
  done <<< "$ENTRIES"
  printf 'iptables -P OUTPUT DROP\n'
  printf 'ip6tables -P OUTPUT ACCEPT\nip6tables -F OUTPUT\n'
  printf 'ip6tables -A OUTPUT -o lo -j ACCEPT\n'
  printf 'ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT\n'
  printf 'ip6tables -P OUTPUT DROP\n'
}

render_check() {
  # the probe script `check` runs inside the container (bash); prints PASS/FAIL lines
  local domain ip port closed candidate
  cat <<'HEAD'
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
tcp_open() { timeout 6 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }
HEAD
  while read -r domain ip port; do
    printf 'if getent ahostsv4 %s | grep -q "^%s "; then pass "%s resolves to %s"; else fail "%s does not resolve to %s"; fi\n' \
      "$domain" "${ip//./\\.}" "$domain" "$ip" "$domain" "$ip"
    printf 'if tcp_open %s %s; then pass "%s:%s (%s) reachable"; else fail "%s:%s (%s) not reachable"; fi\n' \
      "$ip" "$port" "$ip" "$port" "$domain" "$ip" "$port" "$domain"
    if [[ "$port" == 443 ]]; then
      printf 'code="$(curl -sS -o /dev/null -w "%%{http_code}" --connect-timeout 6 --max-time 20 https://%s/ 2>/dev/null || true)"\n' "$domain"
      printf 'if [ -n "$code" ] && [ "$code" != 000 ]; then pass "https://%s/ answers (HTTP $code)"; else fail "https://%s/ gives no HTTP answer"; fi\n' "$domain" "$domain"
    fi
    # the manual opens a port, not the host: a port the allowlist does not open for this address stays closed
    closed=""
    for candidate in 80 22 8080 8443 21 25 3389; do
      if ! grep -q "^[^ ]* $ip $candidate\$" <<< "$ENTRIES"; then closed="$candidate"; break; fi
    done
    if [[ -n "$closed" ]]; then
      printf 'if tcp_open %s %s; then fail "%s:%s reachable but not allowlisted"; else pass "%s:%s blocked"; fi\n' \
        "$ip" "$closed" "$ip" "$closed" "$ip" "$closed"
    fi
  done <<< "$ENTRIES"
  cat <<'TAIL'
for name in pypi.org files.pythonhosted.org github.com registry.npmjs.org mirrors.aliyun.com www.baidu.com passport.threatbook.cn; do
  if getent ahosts "$name" >/dev/null 2>&1; then fail "$name resolves"; else pass "$name does not resolve"; fi
done
# public addresses, and passport.threatbook.cn's (the manual opens it for the admin PC only)
for target in 1.1.1.1:443 223.5.5.5:53 151.101.0.223:443 106.75.36.224:443 123.59.51.113:443; do
  if tcp_open "${target%:*}" "${target#*:}"; then fail "$target reachable"; else pass "$target blocked"; fi
done
# UDP: a DNS question straight to public resolvers must get no answer
udp_dns_answers() {
  [ "$(timeout 6 bash -c "exec 3<>/dev/udp/$1/53 && printf '\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01' >&3 && head -c 1 <&3" 2>/dev/null | wc -c)" -gt 0 ]
}
for server in 8.8.8.8 223.5.5.5 114.114.114.114; do
  if udp_dns_answers "$server"; then fail "UDP DNS to $server answered"; else pass "UDP DNS to $server blocked"; fi
done
exit "$((fails > 0))"
TAIL
}

verify_rules() {
  # `iptables -S OUTPUT` / `ip6tables -S OUTPUT` on stdin ($1 = 4 or 6): policy DROP, and the only
  # ACCEPT rules are loopback, replies and (IPv4) exactly the allowlisted address:port pairs
  local family="$1" line target policy="" bad=0
  while IFS= read -r line; do
    case "$line" in
      "") ;;
      "-P OUTPUT "*) policy="${line#-P OUTPUT }" ;;
      "-A OUTPUT "*)
        # judged by its target: DROP / REJECT can only take traffic away; anything else (ACCEPT
        # beyond the rendered rules, a jump or goto to another chain, no target) is not something
        # `up` writes, so it fails. `up` never writes comments or gotos and one -j per rule, so a
        # rule with a comment (free text that can fake " -j DROP "), a goto or two -j fails too
        target=""
        case "$line" in
          *--comment*|*" -g "*|*" --goto "*) ;;
          *" -j "*)
            target="${line#* -j }"
            if [[ "$target" == *" -j "* ]]; then target=""; else target="${target%% *}"; fi
            ;;
        esac
        case "$target" in
          DROP|REJECT) continue ;;
        esac
        case "$line" in
          "-A OUTPUT -o lo -j ACCEPT"|"-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"|"-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT") continue ;;
        esac
        if [[ "$family" == 4 && "$line" =~ ^-A\ OUTPUT\ -d\ ([0-9.]+)/32\ -p\ tcp\ (-m\ tcp\ )?--dport\ ([0-9]+)\ -j\ ACCEPT$ ]] \
          && grep -q "^[^ ]* ${BASH_REMATCH[1]} ${BASH_REMATCH[3]}\$" <<< "$ENTRIES"; then
          continue
        fi
        printf 'FAIL  IPv%s OUTPUT rule not written by intranet-sim: %s\n' "$family" "$line"
        bad=1
        ;;
      *)
        printf 'FAIL  IPv%s unexpected line in OUTPUT: %s\n' "$family" "$line"
        bad=1
        ;;
    esac
  done
  if [[ "$policy" == DROP ]]; then
    printf 'PASS  IPv%s OUTPUT policy is DROP\n' "$family"
  else
    printf 'FAIL  IPv%s OUTPUT policy is %s, not DROP\n' "$family" "${policy:-missing}"
    bad=1
  fi
  if [[ "$bad" -eq 0 ]]; then printf 'PASS  IPv%s OUTPUT accepts only loopback, replies and the allowlist\n' "$family"; fi
  return "$bad"
}

gateway_is_ours() {
  [[ "$("$DOCKER" inspect -f '{{ index .Config.Labels "flocks.intranet-sim" }}' "$1" 2>/dev/null || true)" == "1" ]]
}

cmd_up() {
  local setup publish_args=() mapping
  [[ -n "$NAME" ]] || die "up needs --name GATEWAY"
  setup="$(render)"   # validates the allowlist before anything is created
  if "$DOCKER" inspect "$NAME" >/dev/null 2>&1; then
    die "container $NAME already exists (intranet-sim.sh down --name $NAME)"
  fi
  for mapping in ${PUBLISH[@]+"${PUBLISH[@]}"}; do publish_args+=(-p "$mapping"); done
  "$DOCKER" create --name "$NAME" --label "$LABEL" --cap-add NET_ADMIN \
    ${publish_args[@]+"${publish_args[@]}"} "$GW_IMAGE" sleep infinity >/dev/null
  if ! "$DOCKER" start "$NAME" >/dev/null; then
    "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
    die "the gateway $NAME did not start"
  fi
  if ! "$DOCKER" exec "$NAME" apk add --no-cache iptables ip6tables dnsmasq >/dev/null 2>&1; then
    "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
    die "could not install iptables / dnsmasq in the gateway (the test host needs internet for apk once)"
  fi
  if ! printf '%s\n' "$setup" | "$DOCKER" exec -i "$NAME" sh -s; then
    "$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true
    die "applying the egress rules in $NAME failed"
  fi
  info "gateway $NAME up; allowed: $(awk '{printf "%s%s(%s):%s", sep, $1, $2, $3; sep=", "}' <<< "$ENTRIES")"
  info "start the server with: docker run --network container:$NAME ..."
}

cmd_check() {
  local mode owner probes=0 rules=0
  [[ -n "$TARGET" ]] || die "check needs a container name"
  render_check | "$DOCKER" exec -i "$TARGET" bash -s || probes=1
  # the rules themselves, read in the namespace owner: covers what probing from here cannot,
  # e.g. UDP to other servers or IPv6 on a test host that has no IPv6 route at all
  mode="$("$DOCKER" inspect -f '{{.HostConfig.NetworkMode}}' "$TARGET" 2>/dev/null || true)"
  if [[ "$mode" == container:* ]]; then
    owner="${mode#container:}"
  elif gateway_is_ours "$TARGET"; then
    owner="$TARGET"
  else
    printf 'FAIL  %s does not run in an intranet-sim gateway namespace (network mode %s)\n' "$TARGET" "${mode:-unknown}"
    return 1
  fi
  "$DOCKER" exec "$owner" iptables -S OUTPUT | verify_rules 4 || rules=1
  "$DOCKER" exec "$owner" ip6tables -S OUTPUT | verify_rules 6 || rules=1
  [[ "$probes" -eq 0 && "$rules" -eq 0 ]]
}

cmd_down() {
  [[ -n "$NAME" ]] || die "down needs --name GATEWAY"
  if ! "$DOCKER" inspect "$NAME" >/dev/null 2>&1; then
    return 0
  fi
  gateway_is_ours "$NAME" || die "$NAME was not started by intranet-sim.sh; not removing it"
  "$DOCKER" rm -f "$NAME" >/dev/null
}

COMMAND="${1:-}"
[[ -n "$COMMAND" ]] || { usage; exit 2; }
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name|--allowlist|--publish|--family) [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value" ;;
  esac
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --allowlist) ALLOWLIST="$2"; shift 2 ;;
    --publish) PUBLISH+=("$2"); shift 2 ;;
    --family) FAMILY="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) if [[ -z "$TARGET" ]]; then TARGET="$1"; shift; else die "unexpected argument: $1"; fi ;;
  esac
done

case "$COMMAND" in
  up|check|render|render-check|verify-rules) load_entries ;;
esac
case "$COMMAND" in
  up) cmd_up ;;
  check) cmd_check ;;
  down) cmd_down ;;
  render) render ;;
  render-check) render_check ;;
  verify-rules) [[ "$FAMILY" == 4 || "$FAMILY" == 6 ]] || die "verify-rules needs --family 4 or 6"; verify_rules "$FAMILY" ;;
  -h|--help|help) usage ;;
  *) die "unknown command: $COMMAND (up, check, down, render, render-check, verify-rules)" ;;
esac
