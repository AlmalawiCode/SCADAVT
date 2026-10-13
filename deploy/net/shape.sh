#!/bin/sh
# shape.sh IFACE [RATE] [DELAY] - token bucket rate limit and/or netem delay
IF=$1; RATE=$2; DELAY=$3
tc qdisc del dev "$IF" root 2>/dev/null || true
if [ -n "$DELAY" ] && [ -n "$RATE" ]; then
  tc qdisc add dev "$IF" root handle 1: netem delay "$DELAY"
  tc qdisc add dev "$IF" parent 1: handle 2: tbf rate "$RATE" burst 32kbit latency 400ms
elif [ -n "$DELAY" ]; then
  tc qdisc add dev "$IF" root netem delay "$DELAY"
elif [ -n "$RATE" ]; then
  tc qdisc add dev "$IF" root tbf rate "$RATE" burst 32kbit latency 400ms
fi
