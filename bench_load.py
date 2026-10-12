#!/usr/bin/env python3
"""Measure SkyrimSE time-to-main-menu and save-load time: MO2 (usvfs) vs flatten.

Start it in its own terminal and leave it running; then launch the game either way
(MO2 -> Run SKSE, or rfad-play.command), load the same save, quit. Every run is one
CSV row. The mode is detected, not typed: flatten state present = "flatten", else "mo2"
(MO2's own process may already be gone by the time the game runs; usvfs lives in the game).

Markers come from SKSE plugin logs of this build (RfaD):
  menu  QuickLootIE.log    "[hh:mm:ss.mmm] ... kDataLoaded end"  (own timestamp)
  load  refrain_nextgen.log "log_pre_load_game" -> "kPostLoadGame" (time the line appears)

    python3 bench_load.py                  # record runs until Ctrl+C
    python3 bench_load.py --summary        # median / min / max per mode
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import statistics
import time
from pathlib import Path

from mo2_flatten import windows_processes

DRIVE_C = Path.home() / "Library/Application Support/CrossOver/Bottles/Games/drive_c"
MYGAMES = DRIVE_C / "users/crossover/Documents/My Games/Skyrim Special Edition/SKSE"
FLATTEN_STATE = DRIVE_C / "Games/RfaD/.mo2flatten/state.json"
MENU_LOG, LOAD_LOG = "QuickLootIE.log", "refrain_nextgen.log"
MENU_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\.(\d{3})\].*kDataLoaded end")
PRE_LOAD, POST_LOAD = "log_pre_load_game", "kPostLoadGame"
POLL = 0.1
FIELDS = ["date", "mode", "menu_s", "load_s", "extra_loads_s"]


class Tail:
    """New lines appended to a log since the last call; survives the game truncating it on start."""

    def __init__(self, path: Path):
        self.path, self.off = path, path.stat().st_size if path.exists() else 0

    def lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self.off:
            self.off = 0
        if size == self.off:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.off)
            chunk = f.read(size - self.off)
        cut = chunk.rfind(b"\n") + 1          # keep a half-written last line for the next poll
        self.off += cut
        return chunk[:cut].decode("utf-8", "replace").splitlines()


def game_running() -> bool:
    return bool(windows_processes(("SkyrimSE.exe",)))


def stamp_to_epoch(m: re.Match, near: float) -> float:
    h, mi, s, ms = map(int, m.groups())
    day = dt.datetime.fromtimestamp(near).replace(hour=h, minute=mi, second=s, microsecond=ms * 1000)
    t = day.timestamp()
    return t + 86400 if t < near - 43200 else t     # run crossed midnight


def record_run(logs: Path) -> dict:
    menu_tail, load_tail = Tail(logs / MENU_LOG), Tail(logs / LOAD_LOG)
    while not game_running():
        time.sleep(POLL)
    t0 = time.time()
    mode = "flatten" if FLATTEN_STATE.exists() else "mo2"
    print(f"\n[{time.strftime('%H:%M:%S')}] SkyrimSE.exe started, mode={mode}")
    menu = pre = None
    loads: list[float] = []
    while game_running():
        for line in menu_tail.lines():
            m = MENU_RE.match(line)
            if m and menu is None:
                menu = stamp_to_epoch(m, t0) - t0
                print(f"  main menu data loaded: {menu:.1f}s after start")
        for line in load_tail.lines():
            if PRE_LOAD in line:
                pre = time.time()
            elif POST_LOAD in line and pre is not None:
                loads.append(time.time() - pre)
                print(f"  save loaded in {loads[-1]:.1f}s")
                pre = None
        time.sleep(POLL)
    print(f"  game exited after {time.time() - t0:.0f}s")
    return {"date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "mode": mode,
            "menu_s": f"{menu:.2f}" if menu is not None else "",
            "load_s": f"{loads[0]:.2f}" if loads else "",
            "extra_loads_s": " ".join(f"{x:.2f}" for x in loads[1:])}


def summary(csv_path: Path) -> None:
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    print(f"{'mode':8} {'metric':7} {'n':>2} {'median':>7} {'min':>6} {'max':>6}")
    for mode in sorted({r["mode"] for r in rows}):
        for metric in ("menu_s", "load_s"):
            v = [float(r[metric]) for r in rows if r["mode"] == mode and r[metric]]
            if v:
                print(f"{mode:8} {metric[:-2]:7} {len(v):>2} {statistics.median(v):7.1f} "
                      f"{min(v):6.1f} {max(v):6.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, default=MYGAMES, help="My Games/.../SKSE log folder")
    ap.add_argument("--csv", type=Path, default=Path(__file__).with_name("bench_results.csv"))
    ap.add_argument("--summary", action="store_true", help="print stats from the CSV and exit")
    args = ap.parse_args()
    if args.summary:
        return summary(args.csv)
    if game_running():
        print("SkyrimSE.exe is already running: start this first, then the game.")
        return
    print(f"waiting for SkyrimSE.exe… results -> {args.csv}  (Ctrl+C to stop)")
    try:
        while True:
            row = record_run(args.logs)
            new = not args.csv.exists()
            with open(args.csv, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
            print(f"  saved: {row}")
    except KeyboardInterrupt:
        print()
        if args.csv.exists():
            summary(args.csv)


if __name__ == "__main__":
    main()
