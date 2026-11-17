"""Windows press-and-hold helper (runs quietly in the background, started at sign-in).

Hold the left mouse button still on a file in File Explorer or on the desktop:
    0.2 s   a ring starts filling around the pointer
    0.55 s  glass branches grow out of the file, one per format (glass_tree.py)
Keep holding, slide onto a format and let go to convert it. Let go anywhere else and the
branches stay open, so you can tap a format, or tap outside to close them.

How it works: a low-level mouse hook watches the left button. When a hold completes, the helper
sends Explorer a normal mouse-up (so Explorer just sees a click that selects the file and never
starts dragging), reads the selected files through the Shell COM interface, and swallows your
real mouse-up so it can be used to pick a format. Moving the mouse before the ring fills, or
clicking quickly, behaves exactly as usual.
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .formats import common_targets
from .system import app_dir, get_logger, settings
from .ui import notify

AGENT_TITLE = "FileConvert Agent"
MAGIC = 0xFC0FC0                      # tags the mouse-up we inject, so our own hook ignores it

WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN = 0x200, 0x201, 0x202, 0x204
WM_QUIT, WM_CLOSE = 0x12, 0x10
LLMHF_INJECTED = 0x1
MOUSEEVENTF_LEFTUP = 0x0004
GA_ROOT = 2

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


HOOKPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def _setup_signatures():
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.CreateMutexW.restype = wintypes.HANDLE


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _file_view_under(x, y):
    """Root window of an Explorer / desktop file view under the point, else None."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    node, in_view = hwnd, False
    while node:
        if _class_name(node) == "SHELLDLL_DefView":           # the file list (not nav pane / ribbon)
            in_view = True
            break
        node = user32.GetParent(node)
    if not in_view:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT)
    return root if _class_name(root) in ("CabinetWClass", "ExploreWClass", "Progman", "WorkerW") else None


def _work_area(x, y):
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    mon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)    # MONITOR_DEFAULTTONEAREST
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def _send_left_up():
    inp = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, MAGIC))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ----------------------------------------------------------------------------- selected files

def selected_files(root_hwnd, shell=None):
    """Paths selected in the Explorer window (or on the desktop) whose top-level window is root_hwnd.
    Tries several ways, because each can fail on some Windows setups."""
    log = get_logger()
    desktop = _class_name(root_hwnd) in ("Progman", "WorkerW")
    attempts = ([("desktop list", _desktop_listview_selection), ("desktop COM", lambda: _desktop_com_selection())]
                if desktop else
                [("Shell COM", lambda: _explorer_com_selection(root_hwnd, shell)),
                 ("PowerShell", lambda: _explorer_powershell_selection(root_hwnd))])
    for name, fn in attempts:
        started = time.time()
        try:
            paths = [p for p in fn() if p]
            log.info("Selection via %s: %d item(s) in %.2fs", name, len(paths), time.time() - started)
            if paths:
                return paths
        except Exception as e:
            log.warning("Selection via %s failed: %s", name, e)
    return []


def _explorer_com_selection(root_hwnd, shell=None):
    import comtypes.client
    shell = shell or comtypes.client.CreateObject("Shell.Application", dynamic=True)
    title = _window_text(root_hwnd)
    found, preferred = [], []
    windows = shell.Windows()
    for i in range(windows.Count):
        try:
            w = windows.Item(i)
            if w is None or int(w.HWND) != int(root_hwnd):
                continue
            items = w.Document.SelectedItems()
            paths = [items.Item(k).Path for k in range(items.Count)]
            if not paths:
                continue
            found = found or paths
            if title.startswith(str(w.LocationName)):          # the visible tab (Windows 11 tabs)
                preferred = paths
        except Exception:
            continue
    return preferred or found


_PS_SCRIPT = (
    "$h=[int64]$env:FC_HWND; (New-Object -ComObject Shell.Application).Windows() | "
    "Where-Object { [int64]$_.HWND -eq $h } | ForEach-Object { $_.Document.SelectedItems() } | "
    "ForEach-Object { $_.Path }")


def _explorer_powershell_selection(root_hwnd):
    env = dict(os.environ, FC_HWND=str(int(root_hwnd)))
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                          capture_output=True, text=True, timeout=8, env=env, creationflags=0x08000000)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _desktop_com_selection():
    from comtypes.automation import VARIANT
    from comtypes.client import CreateObject, GetModule
    from comtypes.client.dynamic import Dispatch
    shdocvw = GetModule("shdocvw.dll")
    windows = CreateObject(shdocvw.ShellWindows, interface=shdocvw.IShellWindows)
    result = windows.FindWindowSW(VARIANT(0), VARIANT(), 8, 1)  # CSIDL_DESKTOP, SWC_DESKTOP, NEEDDISPATCH
    disp = result[-1] if isinstance(result, tuple) else result
    items = Dispatch(disp).Document.SelectedItems()
    return [items.Item(k).Path for k in range(items.Count)]


class LVITEMW(ctypes.Structure):
    _fields_ = [("mask", wintypes.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("state", wintypes.UINT), ("stateMask", wintypes.UINT), ("pszText", ctypes.c_void_p),
                ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int), ("lParam", wintypes.LPARAM),
                ("iIndent", ctypes.c_int), ("iGroupId", ctypes.c_int), ("cColumns", wintypes.UINT),
                ("puColumns", ctypes.c_void_p), ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int)]


def _desktop_listview():
    """The desktop's icon list (a SysListView32 inside Progman or a WorkerW window)."""
    find = user32.FindWindowExW
    find.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    find.restype = wintypes.HWND
    hosts = [user32.FindWindowW("Progman", None)]
    w = None
    while True:
        w = find(None, w, "WorkerW", None)
        if not w:
            break
        hosts.append(w)
    for host in hosts:
        view = find(host, None, "SHELLDLL_DefView", None) if host else None
        lv = find(view, None, "SysListView32", None) if view else None
        if lv:
            return lv
    return None


def _desktop_folders():
    buf = ctypes.create_unicode_buffer(260)
    folders = []
    for csidl in (0x10, 0x19):                       # your Desktop (OneDrive aware), Public Desktop
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            folders.append(Path(buf.value))
    return folders


def _desktop_listview_selection():
    """Read the selected icon names straight from the desktop list, then find those files."""
    lv = _desktop_listview()
    if not lv:
        return []
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = LRESULT
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    k32 = kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.VirtualAllocEx.restype = ctypes.c_void_p
    k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    k32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    proc = k32.OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, False, pid.value)
    if not proc:
        return []
    names = []
    remote = k32.VirtualAllocEx(proc, None, 4096, 0x1000, 0x04)
    try:
        text_at = remote + 512
        i = -1
        all_ones = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1
        for _ in range(500):
            i = send(lv, 0x100C, i if i >= 0 else all_ones, 0x0002)    # LVM_GETNEXTITEM, LVNI_SELECTED
            if i < 0:
                break
            item = LVITEMW(mask=0x0001, iItem=i, iSubItem=0, pszText=text_at, cchTextMax=1024)
            k32.WriteProcessMemory(proc, remote, ctypes.byref(item), ctypes.sizeof(item), None)
            send(lv, 0x1073, i, remote)                                     # LVM_GETITEMTEXTW
            buf = ctypes.create_unicode_buffer(1024)
            k32.ReadProcessMemory(proc, text_at, buf, ctypes.sizeof(buf), None)
            if buf.value:
                names.append(buf.value)
    finally:
        k32.VirtualFreeEx(proc, remote, 0, 0x8000)
        k32.CloseHandle(proc)
    paths = []
    for name in names:                     # names may hide the extension ("holiday" for holiday.mov)
        for folder in _desktop_folders():
            exact = folder / name
            if exact.is_file():
                paths.append(str(exact))
                break
            matches = [m for m in folder.glob(name + ".*") if m.is_file() and m.suffix.lower() != ".lnk"]
            if matches:
                paths.append(str(matches[0]))
                break
    return paths


# ----------------------------------------------------------------------------- the agent

class Agent:
    def __init__(self):
        s = settings()
        self.hold = max(0.3, int(s.get("hold_ms", 550)) / 1000)
        self.events = queue.Queue()
        self.requests = queue.Queue()
        self.log = get_logger()
        self.pressing = False        # left button down on a file view, still waiting
        self.captured = False        # hold fired: we own this press until the real mouse-up
        self.down = None
        self.window = None
        self.timers = []

    # Runs on the hook thread: keep it tiny and never block.
    def _hook(self, n_code, w_param, l_param):
        if n_code == 0:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            injected = info.flags & LLMHF_INJECTED
            x, y = info.pt.x, info.pt.y
            if not injected:
                if w_param == WM_LBUTTONDOWN:
                    if self.window is not None and not self.captured:
                        self.events.put(("click", x, y))
                    elif not self.captured:
                        root = _file_view_under(x, y)
                        if root:
                            self.pressing, self.down = True, (x, y, root)
                            self.events.put(("down", x, y, root))
                elif w_param == WM_MOUSEMOVE:
                    if self.captured:
                        self.events.put(("move", x, y))
                    elif self.pressing and self.down and (abs(x - self.down[0]) > 6 or abs(y - self.down[1]) > 6):
                        self.pressing = False
                        self.events.put(("cancel",))
                elif w_param == WM_LBUTTONUP:
                    if self.captured:
                        self.captured = False
                        self.events.put(("release", x, y))
                        return 1                                # swallow: Explorer already got its click
                    if self.pressing:
                        self.pressing = False
                        self.events.put(("cancel",))
                elif w_param == WM_RBUTTONDOWN and self.pressing:
                    self.pressing = False
                    self.events.put(("cancel",))
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _hook_thread(self):
        self._proc = HOOKPROC(self._hook)                       # keep a reference alive
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self.log.error("Couldn't install the mouse hook (%s)", ctypes.get_last_error())
            return
        self.hook_thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    # Everything below runs on the Tk (main) thread.
    def _later(self, ms, fn):
        self.timers.append(self.tk.after(ms, fn))

    def _cancel_timers(self):
        for t in self.timers:
            try:
                self.tk.after_cancel(t)
            except Exception:
                pass
        self.timers = []

    def _close_window(self):
        if self.window:
            self.window.destroy()
            self.window = None

    def _charge(self):
        if not self.pressing or self.window:
            return
        from .tree_window import TreeWindow
        x, y, _ = self.down
        try:
            self.window = TreeWindow(self.tk, (x, y), _work_area(x, y), self.scale, self.theme,
                                     hold_left=max(0.05, self.hold - 0.2), on_choose=self._convert,
                                     on_closed=self._window_closed)
        except Exception:
            self.log.exception("Couldn't open the branch window")
            self.window = None

    def _selection_worker(self):
        """Runs for the helper's lifetime: COM is set up once, so holds get an answer quickly."""
        shell = None
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            self.log.info("Shell COM ready")
        except Exception as e:
            self.log.warning("Shell COM unavailable (%s); using fallbacks", e)
        while True:
            root = self.requests.get()
            if root is None:
                break
            try:
                paths = selected_files(root, shell)
            except Exception:
                self.log.exception("Couldn't read the Explorer selection")
                paths = []
            self.events.put(("selection", paths))

    def _fire(self):
        if not self.pressing:
            return
        self.pressing = False
        self.captured = True
        _send_left_up()                                         # Explorer: a plain click (selects)
        self.log.info("Hold at %s,%s on %s", self.down[0], self.down[1], _class_name(self.down[2]))
        self.requests.put(self.down[2])

    def _selection(self, paths):
        files = [p for p in paths if os.path.isfile(p)]
        options = common_targets([Path(p).suffix for p in files], self.engines) if files else []
        if not options or not self.window:
            if not files:
                text = "Couldn't tell which file this is"
            else:
                kinds = ", ".join(sorted({(Path(f).suffix.lstrip(".") or "this").upper() for f in files}))
                text = (f"{kinds} files can't be converted yet" if "," not in kinds
                        else "These files have no format in common")
                if any(Path(f).suffix.lower() in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".rtf")
                       for f in files):
                    text = "Word/Excel/PowerPoint need LibreOffice installed"
            self.log.info("Hold menu: %s (%s)", text, files)
            if self.window:
                self.window.show_message(text)         # stays near the pointer, then fades
            else:
                notify(text, "FileConvert", force=True)
            return
        self.files = files
        self.window.open(options, files)

    def _convert(self, ext, files):
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "convert", "--to", ext] + files
        else:
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            cmd = [str(pyw if pyw.exists() else exe), str(app_dir() / "launcher.py"), "convert", "--to", ext] + files
        self.log.info("Hold menu: %s -> %s", files, ext)
        subprocess.Popen(cmd, creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | NO_WINDOW
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _window_closed(self):
        self.window = None

    def _pump(self):
        try:
            self._pump_events()
        except Exception:
            self.log.exception("Hold helper error")
            self.captured = False
            self._close_window()
        self.tk.after(8, self._pump)

    def _pump_events(self):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "down":
                self._close_window()
                self._cancel_timers()
                self._later(200, self._charge)
                self._later(int(self.hold * 1000), self._fire)
            elif kind == "cancel":
                self._cancel_timers()
                if self.window and not self.captured:
                    self.window.cancel()
            elif kind == "selection":
                self._selection(ev[1])
            elif kind == "move" and self.window:
                self.window.hover_screen(ev[1], ev[2])
            elif kind == "release" and self.window:
                self.window.release_screen(ev[1], ev[2])
            elif kind == "click" and self.window:
                self.window.click_screen(ev[1], ev[2])

    def run(self):
        import tkinter as tk
        from .glass_hud import _windows_theme
        from .system import available_engines
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            user32.SetProcessDPIAware()
        try:
            self.scale = user32.GetDpiForSystem() / 96
        except AttributeError:
            self.scale = 1.0
        self.theme = _windows_theme()
        self.engines = available_engines()
        self.tk = tk.Tk()
        self.tk.title(AGENT_TITLE)
        self.tk.withdraw()
        self.tk.protocol("WM_DELETE_WINDOW", self.tk.destroy)
        threading.Thread(target=self._hook_thread, daemon=True).start()
        threading.Thread(target=self._selection_worker, daemon=True).start()
        self.tk.after(8, self._pump)
        self.log.info("Hold helper started (hold %.2fs)", self.hold)
        self.tk.mainloop()
        tid = getattr(self, "hook_thread_id", None)
        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)


def run_agent():
    if sys.platform != "win32":
        print("The hold helper is for Windows (macOS uses FileConvertUI --agent).")
        return 1
    _setup_signatures()
    if not settings().get("hold_to_convert", True):
        return 0
    kernel32.CreateMutexW(None, False, "Local\\FileConvertAgent")
    if ctypes.get_last_error() == 183:                          # ERROR_ALREADY_EXISTS
        return 0
    try:
        Agent().run()
    except Exception:
        get_logger().exception("Hold helper crashed")
        return 1
    return 0


def stop_agent():
    """Ask a running helper to quit (used by install/uninstall)."""
    if sys.platform != "win32":
        return False
    _setup_signatures()
    hwnd = user32.FindWindowW(None, AGENT_TITLE)
    if hwnd:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.5)
        return True
    return False
