#!/bin/sh
# Entry point of every emulated node (PLC, MTU, gateway, WDS server).
#   GATEWAY      IP of the router of this node's LAN; becomes the default route
#   EGRESS_RATE  optional rate limit on the node's LAN interface (e.g. 2mbit)
#   EGRESS_DELAY optional one-way delay on the LAN interface (e.g. 5ms)
# The remaining arguments are the program to run.
set -e
if [ -n "$GATEWAY" ]; then
  ip route replace default via "$GATEWAY"
fi
if [ -n "$EGRESS_RATE$EGRESS_DELAY" ] && [ -n "$GATEWAY" ]; then
  IF=$(ip -o route get "$GATEWAY" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
  /opt/scadavt-net/shape.sh "$IF" "$EGRESS_RATE" "$EGRESS_DELAY"
fi
exec "$@"
