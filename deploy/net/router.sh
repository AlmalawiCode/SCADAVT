#!/bin/sh
# Entry point of a router container.
#   ROUTES        static routes, ';'-separated, e.g. "10.200.0.0/24 via 10.100.0.2"
#   LAN_SUBNET    subnet of the router's LAN side (used to find the interface)
#   LAN_RATE      optional rate limit towards the LAN (the access link, e.g. 2mbit)
#   LAN_DELAY     optional one-way delay towards the LAN
#   WAN_DELAY     optional one-way delay on the WAN side
#   CAPTURE       if set, write a pcap of all interfaces to /logs/pcap/$HOSTNAME.pcap
set -e
sysctl -qw net.ipv4.ip_forward=1 2>/dev/null || true
echo "$ROUTES" | tr ';' '\n' | while read -r r; do
  [ -n "$r" ] && ip route replace $r
done
# interface whose IPv4 address starts with the given "a.b.c." prefix
iface_of() { ip -o -4 addr show | awk -v n="$1" 'index($4, n) == 1 {print $2; exit}'; }
if [ -n "$LAN_SUBNET" ]; then
  LANIF=$(iface_of "$(echo "$LAN_SUBNET" | cut -d. -f1-3).")
  [ -n "$LAN_RATE$LAN_DELAY" ] && /opt/scadavt-net/shape.sh "$LANIF" "$LAN_RATE" "$LAN_DELAY"
fi
if [ -n "$WAN_DELAY" ]; then
  WANIF=$(iface_of "10.100.0.")
  /opt/scadavt-net/shape.sh "$WANIF" "" "$WAN_DELAY"
fi
if [ -n "$CAPTURE" ]; then
  mkdir -p /logs/pcap
  exec tcpdump -i any -n -s 128 -U -w "/logs/pcap/$(hostname).pcap" 2>/dev/null
fi
exec sleep infinity
