#!/bin/bash
# Double-click in Finder: flatten RfaD -> launch SKSE -> wait for exit -> restore MO2 mode.
cd "$(dirname "$0")"
python3 ./mo2_flatten.py \
  --mo2 "$HOME/Library/Application Support/CrossOver/Bottles/Games/drive_c/Games/RfaD/MO2" \
  play "$@"
echo
read -r -p "Готово. Нажмите Enter, чтобы закрыть окно."
