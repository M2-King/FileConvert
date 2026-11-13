"""macOS: drives the native glass picker / progress pill (macos/FileConvertUI.swift).

The installer compiles the Swift helper into the data folder. If it isn't there (no Xcode
Command Line Tools) or fails to start, FileConvert falls back to the plain system list.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .system import app_dir, data_dir, get_logger, settings

HELPER_NAME = "FileConvertUI"


def bundled_helper():
    """The helper prebuilt into FileConvert.app (so users don't need Apple's developer tools)."""
    base = getattr(sys, "_MEIPASS", None)
    p = Path(base) / HELPER_NAME if base else None
    return p if p and p.exists() else None


def helper_path():
    return bundled_helper() or data_dir() / HELPER_NAME


def swift_source():
    base = Path(getattr(sys, "_MEIPASS", app_dir()))          # PyInstaller unpacks data here
    return base / "macos" / "FileConvertUI.swift"


def build_helper():
    """Compile the Swift helper. Returns (ok, message)."""
    if bundled_helper():
        return True, "bundled"
    src, out = swift_source(), helper_path()
    if not src.exists():
        return False, f"missing {src}"
    compilers = []
    if shutil.which("xcrun"):
        compilers.append(["xcrun", "swiftc"])
    if shutil.which("swiftc"):
        compilers.append(["swiftc"])
    if not compilers:
        return False, "Swift isn't installed (run: xcode-select --install)"
    log = data_dir() / "glass_build.log"
    # An embedded Info.plist gives the helper a stable identity for macOS permission prompts
    # (Accessibility for the press-and-hold menu, Automation to ask Finder for the held file).
    info = data_dir() / "FileConvertUI-Info.plist"
    import plistlib
    with open(info, "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.fileconvert.ui", "CFBundleName": "FileConvert",
                       "CFBundleDisplayName": "FileConvert", "LSUIElement": True,
                       "NSAppleEventsUsageDescription": "FileConvert asks Finder which file you're holding."}, f)
    for cc in compilers:
        tmp = out.with_suffix(".building")
        cmd = cc + ["-O", "-parse-as-library", str(src), "-o", str(tmp),
                    "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", str(info)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.write_text(f"{cmd}\n{e}")
            continue
        if proc.returncode == 0 and tmp.exists():
            tmp.replace(out)
            out.chmod(0o755)
            return True, "built"
        log.write_text(f"{' '.join(cmd)}\n\n{proc.stdout}\n{proc.stderr}")
    return False, f"Swift build failed (details in {log})"


class GlassPicker:
    """Shows the glass format picker; after a choice it becomes the progress pill.
    Also acts as the Reporter for convert_many()."""

    def __init__(self, proc):
        self.proc = proc

    @classmethod
    def start(cls, files, options):
        helper = helper_path()
        if not helper.exists():
            return None
        files = [str(Path(f).resolve()) for f in files]
        from .tree import caption_for
        config = {
            "files": files,
            "options": [{"ext": t.ext, "label": caption_for(t.label)[0], "caption": caption_for(t.label)[1]}
                        for t in options],
            "legacy": settings().get("mac_glass", "auto") == "classic",
        }
        try:
            proc = subprocess.Popen([str(helper), json.dumps(config)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, bufsize=1)
        except OSError:
            get_logger().exception("Couldn't start the glass picker")
            return None
        return cls(proc)

    def wait_for_choice(self):
        """Returns the chosen extension, None if the user cancelled, or 'error' if the helper died."""
        line = self.proc.stdout.readline().strip()
        if line.startswith("PICK "):
            return line.split()[1]
        if line == "CANCEL":
            return None
        get_logger().error("Glass picker exited without an answer (%r)", line)
        return "error"

    def _send(self, line):
        try:
            self.proc.stdin.write(line.replace("\n", " ") + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    # Reporter interface
    def progress(self, fraction):
        self._send("PROGRESS -" if fraction is None else f"PROGRESS {fraction:.4f}")

    def status(self, text):
        self._send(f"STATUS {text}")

    def finish(self, ok, message, path=None):
        self._send(f"DONE {message}\t{path or ''}" if ok else f"FAIL {message}")

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=8)     # let the pill finish its exit animation
        except subprocess.TimeoutExpired:
            pass
