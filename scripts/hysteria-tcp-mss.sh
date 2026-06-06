#!/usr/bin/env bash
set -euo pipefail

MSS="${HYSTERIA_TCP_MSS:-1360}"
PORTS="${HYSTERIA_TCP_MSS_PORTS:-443 8443}"

drop_rule() {
  local chain="$1" flag="$2" port="$3" mss="$4"
  while iptables -t mangle -C "$chain" -p tcp "$flag" "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$mss" 2>/dev/null; do
    iptables -t mangle -D "$chain" -p tcp "$flag" "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$mss"
  done
}

for port in $PORTS; do
  for old_mss in 1200 1280 1360 1400 1460 "$MSS"; do
    drop_rule PREROUTING --dport "$port" "$old_mss"
    drop_rule POSTROUTING --sport "$port" "$old_mss"
  done

  iptables -t mangle -C PREROUTING -p tcp --dport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS" 2>/dev/null \
    || iptables -t mangle -A PREROUTING -p tcp --dport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS"

  iptables -t mangle -C POSTROUTING -p tcp --sport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS" 2>/dev/null \
    || iptables -t mangle -A POSTROUTING -p tcp --sport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS"
done
