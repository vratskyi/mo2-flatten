#!/bin/bash
# Double-click in Finder: flatten RfaD -> launch SKSE -> wait for exit -> restore MO2 mode.
# excludes.txt next to this file: one Data-relative glob per line, left out of the game (# = comment).
cd "$(dirname "$0")"
EXCLUDES=()
if [ -f excludes.txt ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"; line="$(echo "$line" | xargs)"
    [ -n "$line" ] && EXCLUDES+=(--exclude "$line")
  done < excludes.txt
fi
python3 ./mo2_flatten.py \
  --mo2 "$HOME/Library/Application Support/CrossOver/Bottles/Games/drive_c/Games/RfaD/MO2" \
  "${EXCLUDES[@]}" play "$@"
echo
read -r -p "Готово. Нажмите Enter, чтобы закрыть окно."
