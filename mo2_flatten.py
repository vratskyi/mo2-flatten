#!/usr/bin/env python3
"""mo2_flatten — materialise a Mod Organizer 2 profile into the real game Data folder
with hardlinks, so the game runs WITHOUT usvfs (useful under Wine/CrossOver, where
usvfs is flaky on large modlists).

Commands
  plan     dry run: what would be linked, conflicts, missing mods
  flatten  build the flattened Data (original Data is kept as Data.vanilla)
  restore  undo: harvest game-written files into MO2 overwrite, put Data.vanilla back
  status   current state, and whether the modlist changed since the last flatten
  verify   check that every file in the flattened Data is the expected winner
  which    show which mods provide a path and who wins (like MO2's Data tab)
  run      start skse64_loader.exe through CrossOver (only while flattened)

MO2 must NOT be running while flattened: it would see the flattened Data as vanilla.
Always `restore` before opening MO2 again.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

STATE_DIR = ".mo2flatten"
VANILLA = "<vanilla>"
OVERWRITE = "<overwrite>"
PROFILE_INIS = ("Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini")
LOCAL_APPDATA_FILES = ("plugins.txt", "loadorder.txt")
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
CROSSOVER_WINE = "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"
GAME_EXES = ("SkyrimSE.exe", "skse64_loader.exe")
GAME_PROCESSES = GAME_EXES + ("ModOrganizer.exe", "usvfs_proxy_x64.exe", "usvfs_proxy_x86.exe")


# ----------------------------------------------------------------------------- paths

def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def qt_value(raw: str) -> str:
    """Decode a Qt ini value: @ByteArray(...) wrapper and doubled backslashes."""
    m = re.fullmatch(r'"?@ByteArray\((.*)\)"?', raw.strip())
    val = m.group(1) if m else raw.strip().strip('"')
    return val.replace("\\\\", "\\")


def read_mo2_ini(mo2: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (mo2 / "ModOrganizer.ini").read_text(encoding="utf-8", errors="replace").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in ("selected_profile", "gamePath", "base_directory",
                                 "mod_directory", "overwrite_directory", "profiles_directory"):
            out[k.strip()] = qt_value(v)
    return out


def find_drive_c(p: Path) -> Path:
    for parent in [p, *p.parents]:
        if parent.name == "drive_c":
            return parent
    die(f"{p} is not inside a Wine bottle (no drive_c ancestor)")
    raise AssertionError


def win_to_unix(win: str, drive_c: Path, relative_to: Path) -> Path:
    win = win.replace("/", "\\")
    m = re.match(r"^([A-Za-z]):\\?(.*)$", win)
    if not m:  # relative path, e.g. "..\"
        return (relative_to / win.replace("\\", "/")).resolve()
    letter, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    if letter == "c":
        root = drive_c
    else:
        root = (drive_c.parent / "dosdevices" / f"{letter}:").resolve()
    return (root / rest).resolve()


@dataclass
class Ctx:
    mo2: Path
    game: Path
    profile: Path
    mods: Path
    overwrite: Path
    mygames: Path
    localappdata: Path
    bottle: str

    @property
    def data(self) -> Path:
        return self.game / "Data"

    @property
    def vanilla(self) -> Path:
        return self.game / "Data.vanilla"

    @property
    def state_dir(self) -> Path:
        return self.game / STATE_DIR

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def manifest(self) -> Path:
        return self.state_dir / "manifest.tsv"


def make_ctx(args: argparse.Namespace) -> Ctx:
    mo2 = Path(args.mo2).expanduser().resolve()
    if not (mo2 / "ModOrganizer.ini").is_file():
        die(f"no ModOrganizer.ini in {mo2}")
    ini = read_mo2_ini(mo2)
    for key in ("base_directory", "mod_directory", "overwrite_directory", "profiles_directory"):
        if ini.get(key):
            die(f"ModOrganizer.ini sets a custom {key}={ini[key]!r}; not supported yet")
    drive_c = find_drive_c(mo2)
    game = Path(args.game).expanduser().resolve() if args.game else \
        win_to_unix(ini.get("gamePath", ".."), drive_c, mo2)
    profile_name = args.profile or ini.get("selected_profile")
    if not profile_name:
        die("cannot determine the active profile; pass --profile")
    profile = mo2 / "profiles" / profile_name

    users = [u for u in (drive_c / "users").iterdir() if u.is_dir() and u.name != "Public"]
    if args.wine_user:
        user = drive_c / "users" / args.wine_user
    elif len(users) == 1:
        user = users[0]
    else:
        die(f"several Wine users {[u.name for u in users]}; pass --wine-user")
    bottle = drive_c.parent.name

    ctx = Ctx(mo2=mo2, game=game, profile=profile, mods=mo2 / "mods", overwrite=mo2 / "overwrite",
              mygames=user / "Documents" / "My Games" / args.game_folder,
              localappdata=user / "AppData" / "Local" / args.game_folder, bottle=bottle)
    for p in (ctx.game, ctx.profile, ctx.mods, ctx.overwrite):
        if not p.is_dir():
            die(f"missing directory {p}")
    return ctx


# ----------------------------------------------------------------------------- modlist

def enabled_mods(ctx: Ctx) -> list[str]:
    """Enabled mods, LOWEST priority first.

    MO2 writes modlist.txt highest-priority-first (reverse of the GUI), so the file
    is reversed here. `*` lines are unmanaged (DLC/vanilla) and live in Data already.
    """
    mods = []
    for line in (ctx.profile / "modlist.txt").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("+") and not line.endswith("_separator"):
            mods.append(line[1:])
    mods.reverse()
    missing = [m for m in mods if not (ctx.mods / m).is_dir()]
    if missing:
        die("enabled mods without a folder in mods/: " + ", ".join(missing))
    return mods


def is_hidden(name: str) -> bool:
    return name.lower().endswith(".mohidden")


def scan_source(root: Path, is_mod: bool) -> tuple[list[str], list[str]]:
    """Return (files, dirs) relative to root, as MO2 would expose them in Data."""
    files, dirs = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_hidden(d)]
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        for d in dirnames:
            dirs.append(os.path.join(rel_dir, d))
        for f in filenames:
            low = f.lower()
            if is_hidden(f) or low in JUNK_FILES:
                continue
            if is_mod and not rel_dir and low == "meta.ini":
                continue
            files.append(os.path.join(rel_dir, f))
    return files, dirs


@dataclass
class Plan:
    sources: list[tuple[str, Path]]            # lowest priority first
    winners: dict[str, tuple[int, str]]        # lower(rel) -> (source index, rel)
    providers: dict[str, list[int]]            # lower(rel) -> all source indices
    dirs: dict[str, str]                       # lower(rel) -> rel


def build_plan(ctx: Ctx, vanilla_root: Path, excludes: list[str] | tuple = ()) -> Plan:
    """`excludes`: fnmatch patterns on the Data-relative path (case-insensitive); matching
    files are left out of the flattened Data — for bisecting a bad plugin without touching MO2."""
    sources: list[tuple[str, Path]] = [(VANILLA, vanilla_root)]
    sources += [(m, ctx.mods / m) for m in enabled_mods(ctx)]
    sources.append((OVERWRITE, ctx.overwrite))
    winners: dict[str, tuple[int, str]] = {}
    providers: dict[str, list[int]] = {}
    dirs: dict[str, str] = {}
    for idx, (name, root) in enumerate(sources):
        files, sdirs = scan_source(root, is_mod=name not in (VANILLA, OVERWRITE))
        for d in sdirs:
            dirs.setdefault(d.lower(), d)
        for f in files:
            key = f.lower()
            winners[key] = (idx, f)            # later source = higher priority
            providers.setdefault(key, []).append(idx)
    clashes = [k for k in winners if k in dirs]
    if clashes:
        die("paths that are a file in one mod and a folder in another: " + ", ".join(clashes[:10]))
    for pattern in excludes:
        pat = pattern.replace("\\", "/").lower()
        for key in [k for k in winners if fnmatch.fnmatchcase(k.replace(os.sep, "/"), pat)]:
            del winners[key]
    return Plan(sources, winners, providers, dirs)


# ----------------------------------------------------------------------------- state

def load_state(ctx: Ctx) -> dict | None:
    if ctx.state_file.is_file():
        return json.loads(ctx.state_file.read_text())
    return None


def save_state(ctx: Ctx, state: dict) -> None:
    ctx.state_dir.mkdir(exist_ok=True)
    tmp = ctx.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, ctx.state_file)


def windows_processes(names: tuple[str, ...]) -> list[str]:
    """Wine processes whose image is one of `names`.

    A real Windows process shows up in `ps` as `C:\\path\\name.exe args`; CrossOver's
    wrappers (`wine --cx-app ...`, `winewrapper.exe --run -- ...`) mention the exe only
    as an argument, so matching the FIRST token keeps them out.
    """
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    pattern = re.compile(r"^\s*\d+\s+[A-Za-z]:\\\S*?\\(" + "|".join(map(re.escape, names)) + r")(\s|$)",
                         re.IGNORECASE)
    return [line.strip() for line in out.splitlines() if pattern.match(line)]


def guard_not_running() -> None:
    running = windows_processes(GAME_PROCESSES)
    if running:
        die("close MO2 / the game first. Running:\n" + "\n".join(running))


def fingerprint(ctx: Ctx) -> dict[str, float]:
    files = [ctx.profile / "modlist.txt", ctx.profile / "plugins.txt", ctx.profile / "loadorder.txt"]
    return {f.name: f.stat().st_mtime for f in files if f.exists()}


def find_case_insensitive(directory: Path, name: str) -> Path | None:
    if not directory.is_dir():
        return None
    for entry in directory.iterdir():
        if entry.name.lower() == name.lower():
            return entry
    return None


def ini_get(path: Path, section: str, key: str) -> str | None:
    cur = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].lower()
        elif cur == section.lower() and "=" in s:
            k, _, v = s.partition("=")
            if k.strip().lower() == key.lower():
                return v.strip()
    return None


def ini_set(path: Path, section: str, key: str, value: str) -> None:
    """Set one key keeping the rest of the file byte-for-byte."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cur, sect_end, done = None, None, False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur == section.lower() and sect_end is None:
                sect_end = i
            cur = s[1:-1].lower()
        elif cur == section.lower() and "=" in s and s.partition("=")[0].strip().lower() == key.lower():
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        if cur == section.lower() and sect_end is None:
            lines.append(f"{key}={value}")
        elif sect_end is not None:
            lines.insert(sect_end, f"{key}={value}")
        else:
            lines += ["", f"[{section}]", f"{key}={value}"]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def profile_settings(ctx: Ctx) -> tuple[bool, bool]:
    f = ctx.profile / "settings.ini"
    if not f.is_file():
        return False, False
    local_saves = (ini_get(f, "General", "LocalSaves") or "").lower() == "true"
    local_settings = (ini_get(f, "General", "LocalSettings") or "").lower() == "true"
    return local_saves, local_settings


def saves_dir(ctx: Ctx) -> Path:
    raw = None
    for name in ("SkyrimCustom.ini", "Skyrim.ini"):
        p = ctx.profile / name
        if p.is_file():
            raw = ini_get(p, "General", "sLocalSavePath")
            if raw:
                break
    raw = (raw or "Saves\\").replace("\\", "/").strip("/")
    return ctx.mygames / raw


# ----------------------------------------------------------------------------- commands

def cmd_plan(ctx: Ctx, args: argparse.Namespace) -> None:
    root = ctx.vanilla if load_state(ctx) else ctx.data
    plan = build_plan(ctx, root)
    per_source = [0] * len(plan.sources)
    for idx, _ in plan.winners.values():
        per_source[idx] += 1
    conflicts = sum(1 for v in plan.providers.values() if len(v) > 1)
    print(f"profile:   {ctx.profile.name}")
    print(f"sources:   {len(plan.sources)} (vanilla + {len(plan.sources) - 2} mods + overwrite)")
    print(f"files:     {len(plan.winners)} final, {conflicts} with conflicts")
    print(f"dirs:      {len(plan.dirs)}")
    losers = [(n, c) for (n, _), c in zip(plan.sources, per_source) if c == 0]
    if losers:
        print(f"sources that win no file ({len(losers)}): " + ", ".join(n for n, _ in losers))


def cmd_flatten(ctx: Ctx, args: argparse.Namespace) -> None:
    guard_not_running()
    if load_state(ctx):
        die("already flattened. Run `restore` first (or `flatten --rebuild`).")
    if ctx.vanilla.exists():
        die(f"{ctx.vanilla} exists but there is no state file — inspect it by hand")
    if os.stat(ctx.mods).st_dev != os.stat(ctx.game).st_dev:
        die("mods/ and the game are on different volumes: hardlinks are impossible")

    all_files = len(build_plan(ctx, ctx.data).winners) if args.exclude else None
    plan = build_plan(ctx, ctx.data, args.exclude)
    if all_files is not None:
        print(f"excluded {all_files - len(plan.winners)} files by {args.exclude}")
        if all_files == len(plan.winners):
            die("--exclude matched nothing — check the pattern (path relative to Data)")
    t0 = time.time()
    save_state(ctx, {"status": "building", "profile": ctx.profile.name})
    os.rename(ctx.data, ctx.vanilla)
    plan.sources[0] = (VANILLA, ctx.vanilla)
    ctx.data.mkdir()

    for rel in sorted(plan.dirs.values(), key=lambda r: r.count(os.sep)):
        (ctx.data / rel).mkdir(parents=True, exist_ok=True)
    with open(ctx.manifest, "w", encoding="utf-8") as mf:
        for key in sorted(plan.winners):
            idx, rel = plan.winners[key]
            name, root = plan.sources[idx]
            src, dst = root / rel, ctx.data / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.link(src, dst)
            mf.write(f"{rel}\t{name}\t{os.stat(dst).st_ino}\n")

    placed = place_profile_files(ctx)
    save_state(ctx, {
        "status": "flattened",
        "profile": ctx.profile.name,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": len(plan.winners),
        "mods": [n for n, _ in plan.sources[1:-1]],
        "fingerprint": fingerprint(ctx),
        "excludes": args.exclude,
        "placed": placed,
    })
    if args.exclude:
        print(f"excludes: {args.exclude}")
    print(f"flattened {len(plan.winners)} files from {len(plan.sources)} sources "
          f"in {time.time() - t0:.1f}s")
    for line in placed["log"]:
        print("  " + line)
    print("\nMO2 must stay closed now. Launch:  mo2_flatten.py run   ·   undo:  mo2_flatten.py restore")


def backup_file(ctx: Ctx, path: Path, tag: str) -> str | None:
    if not path.exists():
        return None
    dst = ctx.state_dir / "backup" / tag / path.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    return str(dst)


def place_profile_files(ctx: Ctx) -> dict:
    """Put what MO2 virtualises per-profile where the game looks without MO2."""
    log: list[str] = []
    placed: dict = {"mygames": {}, "localappdata": {}, "saves": str(saves_dir(ctx)), "log": log}
    local_saves, local_settings = profile_settings(ctx)
    ctx.mygames.mkdir(parents=True, exist_ok=True)
    ctx.localappdata.mkdir(parents=True, exist_ok=True)

    if local_settings:
        for name in PROFILE_INIS:
            src = ctx.profile / name
            if not src.is_file():
                continue
            existing = find_case_insensitive(ctx.mygames, name)
            placed["mygames"][name] = backup_file(ctx, existing, "mygames") if existing else None
            if existing:
                existing.unlink()
            shutil.copy2(src, ctx.mygames / name)
        tweaks = ctx.profile / "initweaks.ini"
        if tweaks.is_file():
            apply_ini_tweaks(tweaks, ctx.mygames / "Skyrim.ini", log)
        # what we placed, so restore copies back only real in-game edits, not our tweaks
        placed["placed_hash"] = {n: file_hash(ctx.mygames / n) for n in placed["mygames"]}
        log.append(f"ini from profile -> {ctx.mygames}")

    for name in LOCAL_APPDATA_FILES:
        src = ctx.profile / name
        if src.is_file():
            existing = find_case_insensitive(ctx.localappdata, name)
            placed["localappdata"][name] = backup_file(ctx, existing, "localappdata") if existing else None
            if existing:
                existing.unlink()
            shutil.copy2(src, ctx.localappdata / name)
    log.append(f"plugins.txt/loadorder.txt from profile -> {ctx.localappdata}")

    if local_saves:
        dst = saves_dir(ctx)
        dst.mkdir(parents=True, exist_ok=True)
        n = sync_newer(ctx.profile / "saves", dst, move=False)
        log.append(f"profile saves -> {dst} ({n} copied)")
    return placed


def apply_ini_tweaks(tweaks: Path, target: Path, log: list[str]) -> None:
    if not target.is_file():
        return
    cur = None
    for line in tweaks.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
        elif cur and "=" in s and not s.startswith(";"):
            k, _, v = s.partition("=")
            ini_set(target, cur, k.strip(), v.strip())
            log.append(f"initweaks: [{cur}] {k.strip()}={v.strip()}")


def sync_newer(src: Path, dst: Path, move: bool) -> int:
    """Copy/move files from src to dst when missing or newer in src. Never deletes a newer file."""
    if not src.is_dir():
        return 0
    n = 0
    for f in src.rglob("*"):
        if not f.is_file() or f.name.lower() in JUNK_FILES:
            continue
        target = dst / f.relative_to(src)
        if target.exists() and target.stat().st_mtime >= f.stat().st_mtime:
            same = target.stat().st_size == f.stat().st_size and target.read_bytes() == f.read_bytes()
            if move and same:
                f.unlink()                            # identical copy already in place
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            os.replace(f, target)
        else:
            shutil.copy2(f, target)
        n += 1
    return n


def read_manifest(ctx: Ctx) -> dict[str, tuple[str, str, int]]:
    out = {}
    if ctx.manifest.is_file():
        for line in ctx.manifest.read_text(encoding="utf-8").splitlines():
            rel, src, ino = line.split("\t")
            out[rel.lower()] = (rel, src, int(ino))
    return out


def cmd_restore(ctx: Ctx, args: argparse.Namespace) -> None:
    guard_not_running()
    state = load_state(ctx)
    if not state:
        die("not flattened (no state file)")
    if not ctx.vanilla.is_dir():
        die(f"{ctx.vanilla} is missing — refusing to touch Data")
    manifest = read_manifest(ctx)
    harvested, orphans, removed = [], [], 0

    if ctx.data.is_dir():
        for dirpath, dirnames, filenames in os.walk(ctx.data):
            for f in filenames:
                path = Path(dirpath) / f
                rel = str(path.relative_to(ctx.data))
                st = os.lstat(path)
                entry = manifest.get(rel.lower())
                if f.lower() in JUNK_FILES:
                    path.unlink()
                elif entry and st.st_ino == entry[2] and st.st_nlink > 1:
                    path.unlink()                     # our hardlink; the original stays in place
                    removed += 1
                elif entry and st.st_ino == entry[2]:
                    move_to(path, ctx.overwrite / "_flatten_orphans" / rel)   # source vanished
                    orphans.append(rel)
                else:
                    move_to(path, ctx.overwrite / rel)  # written or replaced by the game
                    harvested.append(rel)
        for dirpath, _, _ in sorted(os.walk(ctx.data), key=lambda t: -t[0].count(os.sep)):
            os.rmdir(dirpath)                         # fails loudly if anything is left
    os.rename(ctx.vanilla, ctx.data)

    log = restore_profile_files(ctx, state.get("placed", {}))
    if ctx.manifest.exists():
        ctx.manifest.replace(ctx.state_dir / "manifest.last.tsv")
    ctx.state_file.unlink()

    print(f"restored: {removed} links removed, Data.vanilla -> Data")
    if harvested:
        print(f"{len(harvested)} files written by the game moved to MO2 overwrite:")
        for r in harvested[:30]:
            print("  " + r)
    if orphans:
        print(f"WARNING: {len(orphans)} files had lost their source; kept in overwrite/_flatten_orphans")
    for line in log:
        print("  " + line)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def move_to(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def restore_profile_files(ctx: Ctx, placed: dict) -> list[str]:
    log = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    placed_hash = placed.get("placed_hash", {})
    for name, backup in placed.get("mygames", {}).items():
        live = find_case_insensitive(ctx.mygames, name)
        prof = ctx.profile / name
        if live and prof.is_file() and file_hash(live) != placed_hash.get(name):
            backup_file(ctx, prof, f"profile-{stamp}")
            shutil.copy2(live, prof)                  # keep settings changed in game
            log.append(f"{name}: in-game changes copied back to the profile")
        if live:
            live.unlink()
        if backup:
            shutil.copy2(backup, ctx.mygames / Path(backup).name)
    for name, backup in placed.get("localappdata", {}).items():
        live = find_case_insensitive(ctx.localappdata, name)
        if live:
            live.unlink()
        if backup:
            shutil.copy2(backup, ctx.localappdata / Path(backup).name)
    local_saves, _ = profile_settings(ctx)
    if local_saves and placed.get("saves"):
        n = sync_newer(Path(placed["saves"]), ctx.profile / "saves", move=True)
        log.append(f"saves: {n} new/updated moved into the profile")
    return log


def cmd_status(ctx: Ctx, args: argparse.Namespace) -> None:
    state = load_state(ctx)
    if not state:
        print("state: NOT flattened (normal MO2 mode)")
        return
    print(f"state: {state['status']}  profile={state.get('profile')}  since {state.get('when')}")
    print(f"files: {state.get('files')}  mods: {len(state.get('mods', []))}")
    if state.get("fingerprint") and state["fingerprint"] != fingerprint(ctx):
        print("WARNING: modlist/plugins changed after flattening — run restore + flatten again")


def cmd_verify(ctx: Ctx, args: argparse.Namespace) -> None:
    state = load_state(ctx)
    if not state or state["status"] != "flattened":
        die("not flattened")
    plan = build_plan(ctx, ctx.vanilla, state.get("excludes", []))
    manifest = read_manifest(ctx)
    bad = []
    if set(manifest) != set(plan.winners):
        bad.append(f"file set differs from the current modlist "
                   f"({len(set(plan.winners) - set(manifest))} missing, "
                   f"{len(set(manifest) - set(plan.winners))} extra)")
    for key, (idx, rel) in plan.winners.items():
        dst = ctx.data / rel
        src = plan.sources[idx][1] / rel
        if not dst.exists() or not os.path.samefile(dst, src):
            bad.append(f"{rel}: expected {plan.sources[idx][0]}")
    conflicts = sum(1 for v in plan.providers.values() if len(v) > 1)
    if bad:
        print(f"FAIL: {len(bad)} problems")
        for b in bad[:30]:
            print("  " + b)
        sys.exit(2)
    print(f"OK: all {len(plan.winners)} files are the MO2 winners ({conflicts} conflicts resolved)")


def cmd_which(ctx: Ctx, args: argparse.Namespace) -> None:
    plan = build_plan(ctx, ctx.vanilla if load_state(ctx) else ctx.data)
    for rel in args.paths:
        key = rel.replace("\\", "/").replace("/", os.sep).lower()
        if key not in plan.providers:
            print(f"{rel}: not provided by anything")
            continue
        names = [plan.sources[i][0] for i in plan.providers[key]]
        print(f"{rel}\n  winner: {names[-1]}\n  also in (lower priority): {', '.join(reversed(names[:-1])) or '-'}")


def cmd_run(ctx: Ctx, args: argparse.Namespace) -> None:
    state = load_state(ctx)
    if not state or state["status"] != "flattened":
        die("not flattened — run `flatten` first (or launch through MO2)")
    guard_not_running()
    if state.get("fingerprint") != fingerprint(ctx):
        print("WARNING: modlist/plugins changed after flattening", file=sys.stderr)
    launch(ctx)


def unix_to_win(path: Path, drive_c: Path) -> str:
    return "C:\\" + str(path.resolve().relative_to(drive_c)).replace("/", "\\")


def launch(ctx: Ctx) -> subprocess.Popen:
    # CrossOver's `wine --cx-app` does not accept native paths ("could not find ... Is it
    # installed?"), so both the exe and the workdir are passed as Windows paths.
    drive_c = find_drive_c(ctx.game)
    game_win = unix_to_win(ctx.game, drive_c)
    log = open(ctx.state_dir / "wine.log", "w")
    proc = subprocess.Popen([CROSSOVER_WINE, "--bottle", ctx.bottle, "--workdir", game_win,
                             "--cx-app", game_win + "\\skse64_loader.exe"],
                            start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
    print(f"started {game_win}\\skse64_loader.exe in bottle {ctx.bottle!r} "
          f"(wine output: {ctx.state_dir / 'wine.log'})")
    return proc


def game_running() -> bool:
    return bool(windows_processes(GAME_EXES))


def cmd_play(ctx: Ctx, args: argparse.Namespace) -> None:
    """flatten -> launch -> wait for the game to exit -> restore. MO2 never sees flattened Data."""
    if load_state(ctx):
        cmd_restore(ctx, args)                        # stale flatten from an earlier session
    cmd_flatten(ctx, args)
    proc = launch(ctx)
    deadline = time.time() + 180
    while not game_running():
        rc = proc.poll()
        if rc not in (None, 0):
            print(f"wine failed (exit {rc}):\n" + (ctx.state_dir / "wine.log").read_text()[-2000:],
                  file=sys.stderr)
            break
        if time.time() > deadline:
            print("the game did not start within 3 minutes; restoring", file=sys.stderr)
            break
        time.sleep(2)
    else:
        print("game is running; waiting for it to exit (Ctrl+C leaves Data flattened)")
        while game_running():
            time.sleep(5)
        time.sleep(3)                                 # let wine flush file handles
    if args.keep:
        print("--keep: Data stays flattened; run `restore` before opening MO2")
        return
    cmd_restore(ctx, args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mo2", required=True, help="MO2 instance folder (unix path)")
    ap.add_argument("--profile", help="profile name (default: MO2's selected profile)")
    ap.add_argument("--game", help="game folder (default: gamePath from ModOrganizer.ini)")
    ap.add_argument("--wine-user", help="Wine user under drive_c/users (auto if only one)")
    ap.add_argument("--game-folder", default="Skyrim Special Edition",
                    help="folder name under My Games and AppData/Local")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="leave matching Data-relative paths out of flatten/play (repeatable), "
                         "e.g. 'NetScriptFramework/Plugins/MainMenuRandomizerSE.*'")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "flatten", "restore", "status", "verify", "run", "play"):
        sp = sub.add_parser(name)
        if name == "flatten":
            sp.add_argument("--rebuild", action="store_true", help="restore first, then flatten again")
        if name == "play":
            sp.add_argument("--keep", action="store_true", help="do not restore after the game exits")
    sp = sub.add_parser("which")
    sp.add_argument("paths", nargs="+", help="path relative to Data, e.g. SKSE/Plugins/foo.ini")
    args = ap.parse_args()
    ctx = make_ctx(args)
    if args.cmd == "flatten" and args.rebuild and load_state(ctx):
        cmd_restore(ctx, args)
    {"plan": cmd_plan, "flatten": cmd_flatten, "restore": cmd_restore, "status": cmd_status,
     "verify": cmd_verify, "which": cmd_which, "run": cmd_run, "play": cmd_play}[args.cmd](ctx, args)


if __name__ == "__main__":
    main()
