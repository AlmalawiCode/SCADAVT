#!/usr/bin/env bash
set -euo pipefail
# Launch field-device runners (outside CORE: for local bring-up/testing).
python3 -m scadavt.core.device --session session.json --node PLC1 --modbus 10.0.0.1:502 --io 0.0.0.0:9161 &   # PLC1
python3 -m scadavt.core.master --session session.json --node MTU &   # MTU
wait