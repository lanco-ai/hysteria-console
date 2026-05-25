#!/usr/bin/env bash
set -euo pipefail

MSS="${HYSTERIA_TCP_MSS:-1200}"
PORTS="${HYSTERIA_TCP_MSS_PORTS:-443 8443}"

for port in $PORTS; do
  iptables -t mangle -C PREROUTING -p tcp --dport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS" 2>/dev/null \
    || iptables -t mangle -A PREROUTING -p tcp --dport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS"

  iptables -t mangle -C POSTROUTING -p tcp --sport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS" 2>/dev/null \
    || iptables -t mangle -A POSTROUTING -p tcp --sport "$port" --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$MSS"
done
