"""Command line entry point. The right-click menus call this; you normally never run it yourself.

    FileConvert convert --to png FILE [FILE ...]   convert straight away (Windows submenu uses this)
    FileConvert pick FILE [FILE ...]               pop up a format list, then convert (macOS)
    FileConvert targets FILE                       show what a file can become
    FileConvert install [--classic-menu] [--all-files]
    FileConvert uninstall
    FileConvert doctor                             show which converters were found
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .engines import ENGINES
from .formats import canonical, common_targets, targets_for
from .system import (IS_MAC, IS_WIN, ConversionError, available_engines, data_dir,
                     find_ffmpeg, find_soffice, get_logger, reserve_output, settings)
from .ui import ask_yes_no_cancel, message, notify, pick_format


class Reporter:
    """Feedback while converting. This default one only shows system notifications;
    the glass UIs (glass_hud.py on Windows, glass_mac.py on macOS) replace it."""

    def progress(self, fraction):          # 0..1, or None when the engine can't tell
        pass

    def status(self, text):
        pass

    def finish(self, ok, message, path=None):
        notify(message, "Conversion done" if ok else "Conversion failed", force=not ok)


class StdoutReporter(Reporter):
    """Streams progress as lines (PROGRESS / STATUS / DONE / FAIL) for the macOS hold helper."""

    def _out(self, line):
        try:
            sys.stdout.write(line.replace("\n", " ") + "\n")
            sys.stdout.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def progress(self, fraction):
        self._out("PROGRESS -" if fraction is None else f"PROGRESS {fraction:.4f}")

    def status(self, text):
        self._out(f"STATUS {text}")

    def finish(self, ok, message, path=None):
        self._out(f"DONE {message}\t{path or ''}" if ok else f"FAIL {message}")


def export_targets(engines):
    """Write targets.json (extension -> formats) so the hold helpers can show the menu instantly."""
    from .formats import supported_extensions
    from .tree import caption_for
    table = {}
    for ext, targets in supported_extensions(engines).items():
        table[ext] = [{"ext": t.ext, "label": caption_for(t.label)[0], "caption": caption_for(t.label)[1]}
                      for t in targets]
    path = data_dir() / "targets.json"
    path.write_text(json.dumps(table, indent=1), encoding="utf-8")
    return path


def convert_one(src, target, engines, progress=None):
    src = Path(src)
    if not src.is_file():
        raise ConversionError(f"{src.name} isn't a file")
    options = {t.ext: t for t in targets_for(src.suffix, engines)}
    if target not in options:
        raise ConversionError(f"Can't convert {src.suffix or 'this file'} to {target.upper()}")
    dst = reserve_output(src, target)
    try:
        ENGINES[options[target].engine](src, dst, target, progress=progress)
        if dst.stat().st_size == 0:
            raise ConversionError("The converter produced an empty file")
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst


def convert_many(files, target, engines, reporter=None):
    reporter = reporter or Reporter()
    log = get_logger()
    todo = [Path(f) for f in files if canonical(Path(f).suffix) != target]  # skip files already in it
    done, failed = [], []
    n = max(1, len(todo))
    for i, f in enumerate(todo):
        reporter.status(f.name if n == 1 else f"{i + 1} of {n}  \u00b7  {f.name}")
        reporter.progress(i / n if n > 1 else None)
        started = time.time()
        try:
            out = convert_one(f, target, engines,
                              progress=lambda p, i=i: reporter.progress((i + p) / n))
            done.append(out)
            reporter.progress((i + 1) / n)
            log.info("OK %s -> %s (%.1fs)", f, out, time.time() - started)
        except ConversionError as e:
            failed.append((f.name, str(e)))
            log.error("FAILED %s -> %s: %s", f, target, e)
        except Exception as e:  # never die silently: log + tell the user
            failed.append((f.name, f"{type(e).__name__}: {e}"))
            log.exception("CRASH %s -> %s", f, target)

    if failed:
        name, why = failed[0]
        extra = f" (+{len(failed) - 1} more)" if len(failed) > 1 else ""
        reporter.finish(False, f"{name}: {why[:180]}{extra}")
        return 1
    if done:
        msg = f"Saved {done[0].name}" if len(done) == 1 else f"Converted {len(done)} files to {target.upper()}"
        reporter.finish(True, msg, str(done[0]))
    else:
        reporter.finish(True, f"Already {target.upper()}")
    return 0


def label_for(files, target, engines):
    for t in common_targets([Path(f).suffix for f in files], engines):
        if t.ext == target:
            return t.label
    return target.upper()


def cmd_convert(files, target, engines):
    """Called by the Windows submenu: show the glass HUD while converting (if enabled)."""
    if settings().get("glass_ui", True):
        try:
            from .glass_hud import run_with_hud
            return run_with_hud(lambda rep: convert_many(files, target, engines, rep),
                                label_for(files, target, engines))
        except Exception:  # no Tk, odd display setup... fall back to notifications
            get_logger().exception("Glass HUD unavailable, using notifications")
    return convert_many(files, target, engines)


def cmd_pick(files, engines):
    """Called by the macOS Quick Action: choose a format, then convert."""
    files = [f for f in files if Path(f).is_file()]
    if not files:
        notify("Select one or more files to convert.", force=True)
        return 1
    options = common_targets([Path(f).suffix for f in files], engines)
    if not options:
        kinds = ", ".join(sorted({Path(f).suffix.lower() or "?" for f in files}))
        notify(f"No conversions available for {kinds}", force=True)
        return 1

    if settings().get("glass_ui", True):
        from .glass_mac import GlassPicker
        picker = GlassPicker.start(files, options)
        if picker:
            idx = picker.wait_for_choice()
            if idx is None:
                picker.close()
                return 0
            if idx != "error":
                try:
                    return convert_many(files, idx, engines, picker)
                finally:
                    picker.close()
            picker.close()                    # helper crashed: fall back to the plain list below

    what = Path(files[0]).name if len(files) == 1 else f"{len(files)} files"
    idx = pick_format([t.label for t in options], f"Convert {what} to:")
    if idx is None:
        return 0
    return convert_many(files, options[idx].ext, engines)


def cmd_doctor(engines):
    rows = [
        ("Images (Pillow)", "pillow" in engines, ""),
        ("iPhone HEIC photos (pillow-heif)", "heif" in engines, "pip install pillow-heif"),
        ("Audio & video (ffmpeg)", "ffmpeg" in engines, find_ffmpeg() or "not found"),
        ("Documents (LibreOffice)", "soffice" in engines, find_soffice() or "not found"),
    ]
    lines = [f"FileConvert {__version__}"]
    lines += [f"  [{'x' if ok else ' '}] {name}  {detail}" for name, ok, detail in rows]
    lines.append(f"  Log & settings: {data_dir()}")
    return "\n".join(lines)


def _mac_first_launch():
    """FileConvert.app was opened: install the Quick Action and the press-and-hold helper."""
    import subprocess
    from . import install_macos
    exe = str(Path(sys.executable).resolve())
    if exe.startswith("/Volumes/") or "AppTranslocation" in exe:
        # Opened from the disk image (or a quarantined download): the menus would point at a
        # path that disappears, so ask for the usual drag-to-Applications first.
        text, ok = ("Drag FileConvert into your Applications folder first, then open it from there.", False)
    else:
        try:
            text = install_macos.install(available_engines())
            ok = True
        except Exception as e:
            get_logger().exception("Install failed")
            text, ok = f"Setup didn't finish: {e}", False
    get_logger().info("First launch: %s", text.replace("\n", " | "))
    if ok:
        text = ("FileConvert is ready.\n\nPress and hold a file in Finder for half a second, then slide "
                "onto a format and let go.\n\nIf macOS asks, allow FileConvert in Privacy & Security > "
                "Accessibility, and let it control Finder.\n\nBackup: right-click a file > Quick Actions > "
                "Convert To…")
    script = ['on run argv', 'display dialog (item 1 of argv) with title "FileConvert" buttons {"OK"} '
              'default button "OK" with icon note', 'end run']
    cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + [text]
    subprocess.run(cmd, capture_output=True)
    return 0 if ok else 1


def _installer():
    if IS_WIN:
        from . import install_windows as mod
    elif IS_MAC:
        from . import install_macos as mod
    else:
        raise SystemExit("Right-click menus are installed on Windows and macOS only.")
    return mod


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    frozen = getattr(sys, "frozen", False)
    # Opening the Mac app (FileConvert.app from the .dmg): set everything up and say how to use it.
    if not argv and IS_MAC and frozen:
        return _mac_first_launch()

    # Double-clicking the built FileConvert.exe: offer install / uninstall.
    if not argv and IS_WIN and sys.stdout is None:
        answer = ask_yes_no_cancel("Add 'Convert to' to your right-click menu?\n\n"
                                   "Yes = install / update\nNo = remove it")
        argv = {"yes": ["install"], "no": ["uninstall"]}.get(answer)
        if not argv:
            return 0

    p = argparse.ArgumentParser(prog="FileConvert", description="Silent right-click file converter")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--to", required=True)
    c.add_argument("files", nargs="+")
    sub.add_parser("pick").add_argument("files", nargs="*")
    sub.add_parser("targets").add_argument("file")
    i = sub.add_parser("install")
    i.add_argument("--classic-menu", action="store_true",
                   help="Windows 11: always show the full right-click menu (no 'Show more options')")
    i.add_argument("--all-files", action="store_true",
                   help="macOS: show the Quick Action on every file (helps with .mkv etc.)")
    i.add_argument("--dry-run", action="store_true", help="print what would be registered")
    i.add_argument("--quiet", action="store_true", help="no message when done (used by the installer)")
    r = sub.add_parser("run")
    r.add_argument("--to", required=True)
    r.add_argument("--report", action="store_true", help="print progress lines for the hold helper")
    r.add_argument("files", nargs="+")
    sub.add_parser("agent", help="Windows: run the press-and-hold helper")
    sub.add_parser("uninstall").add_argument("--quiet", action="store_true")
    sub.add_parser("stop-agent", help="Windows: stop the press-and-hold helper (used by the installer)")
    sub.add_parser("doctor")
    args = p.parse_args(argv)

    engines = available_engines()
    try:
        if args.cmd == "convert":
            return cmd_convert(args.files, canonical(args.to), engines)
        if args.cmd == "pick":
            return cmd_pick(args.files, engines)
        if args.cmd == "run":
            return convert_many(args.files, canonical(args.to), engines,
                                StdoutReporter() if args.report else None)
        if args.cmd == "agent":
            from .win_agent import run_agent
            return run_agent()
        if args.cmd == "targets":
            t = targets_for(Path(args.file).suffix, engines)
            print(", ".join(x.label for x in t) or "(nothing)")
            return 0
        if args.cmd == "doctor":
            message(cmd_doctor(engines))
            return 0
        if args.cmd == "install":
            text = _installer().install(engines, classic_menu=args.classic_menu,
                                        all_files=args.all_files, dry_run=args.dry_run)
            get_logger().info("Install: %s", text.replace("\n", " | "))
            if not args.quiet:
                message(text)
            return 0
        if args.cmd == "uninstall":
            text = _installer().uninstall()
            if not args.quiet:
                message(text)
            return 0
        if args.cmd == "stop-agent":
            from .win_agent import stop_agent
            stop_agent()
            return 0
    except Exception as e:
        get_logger().exception("Unexpected error")
        notify(f"{type(e).__name__}: {e}", "FileConvert error", force=True)
        if sys.stdout is not None:
            raise
        return 1
    return 0
