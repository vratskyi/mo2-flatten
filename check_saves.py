#!/usr/bin/env python3
"""Find Skyrim SE saves cut off by the engine's save buffer limit (64 MiB, 128 MiB with Engine Fixes).

A save that outgrows the buffer is written truncated, without any warning, and crashes the game on
load (NSF crash log: SkyrimSE.exe+128731A, LoadStorageWrapper). Such a save fills the buffer exactly:
header + uncompressed body == limit + 8. Only the file header is read, no decompression needed.

    python3 check_saves.py path/to/saves            # every .ess in the folder
    python3 check_saves.py Save123.ess Save124.ess  # selected files
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

MIB = 1 << 20
LIMITS = (64 * MIB, 128 * MIB)          # vanilla; Engine Fixes SaveGameMaxSize = true
WARN_SHARE = 0.94                       # warn above ~60 MiB of 64


def read_header(path: Path) -> dict:
    with open(path, "rb") as f:
        head = f.read(64 * 1024)
        if head[:13] != b"TESV_SAVEGAME":
            raise ValueError("not a Skyrim save")
        hsize = struct.unpack_from("<I", head, 13)[0]
        o = 17
        version, number = struct.unpack_from("<II", head, o); o += 8
        n = struct.unpack_from("<H", head, o)[0]; o += 2
        name = head[o:o + n].decode("utf-8", "replace")
        end = 17 + hsize
        if version < 12:
            raise ValueError(f"save version {version}: not Skyrim SE")
        width, height = struct.unpack_from("<II", head, end - 10)
        compression = struct.unpack_from("<H", head, end - 2)[0]
        body = end + width * height * 4
        if compression == 0:
            return dict(number=number, name=name, used=path.stat().st_size - body + end)
        f.seek(body)
        ulen = struct.unpack("<I", f.read(4))[0]
    # the engine's buffer holds the header and the body, not the two length fields after the screenshot
    return dict(number=number, name=name, used=end + width * height * 4 + ulen)


def verdict(used: int) -> str:
    for limit in LIMITS:
        if used == limit:
            return f"BROKEN: cut off at {limit // MIB} MiB"
    if used > LIMITS[0]:
        return "ok (over 64 MiB: SaveGameMaxSize is on)"
    if used > LIMITS[0] * WARN_SHARE:
        return "ok, close to 64 MiB: turn on SaveGameMaxSize in EngineFixes.toml"
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="save folder(s) or .ess files")
    args = ap.parse_args()
    files: list[Path] = []
    for p in args.paths:
        if not p.exists():
            print(f"{p}: no such file or folder")
            continue
        files +=sorted(p.glob("*.ess"), key=lambda f: f.stat().st_mtime) if p.is_dir() else [p]
    if not files:
        print("no .ess files found")
        return 1
    broken = 0
    for f in files:
        try:
            h = read_header(f)
        except (OSError, ValueError, struct.error) as e:
            print(f"{f.name}: cannot read ({e})")
            continue
        v = verdict(h["used"])
        broken += v.startswith("BROKEN")
        print(f"{f.name[:48]:48} {h['name'][:16]:16} {h['used'] / MIB:8.3f} MiB  {v}")
    print(f"\n{len(files)} saves, {broken} broken")
    return 2 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
