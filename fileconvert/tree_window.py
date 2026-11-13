"""The on-screen window for the press-and-hold branch menu (Windows). Drawing is in glass_tree.py."""
import ctypes
import sys
import time

from .glass_tree import TreeAnimator, TreeRenderer
from .tree import layout

HALF = 300            # logical px around the held point that the branches may use


class TreeWindow:
    def __init__(self, tk_root, center, work, scale, theme, hold_left, on_choose, on_closed):
        import tkinter as tk
        from PIL import ImageGrab, ImageTk
        self._ImageTk = ImageTk
        self.on_choose, self.on_closed = on_choose, on_closed
        cx, cy = center
        left, top, right, bottom = work
        half = int(HALF * scale)
        W, H = min(2 * half, right - left), min(2 * half, bottom - top)
        x0 = min(max(cx - half, left), right - W)
        y0 = min(max(cy - half, top), bottom - H)
        self.origin, self.scale = (x0, y0), scale
        root = (cx - x0, cy - y0)
        self.room = {"left": root[0] / scale, "right": (W - root[0]) / scale,
                     "up": root[1] / scale, "down": (H - root[1]) / scale}
        backdrop = ImageGrab.grab(bbox=(x0, y0, x0 + W, y0 + H), all_screens=True)
        self.renderer = TreeRenderer(backdrop, scale, theme, {"leaves": []}, root)
        self.anim = TreeAnimator(0, time.perf_counter(), hold_left)
        self.files, self.lay, self.closed = [], None, False

        self.top = tk.Toplevel(tk_root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.geometry(f"{W}x{H}{x0:+d}{y0:+d}")
        self.label = tk.Label(self.top, bd=0, highlightthickness=0)
        self.label.pack()
        if sys.platform == "win32":
            self.top.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id())
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            # no taskbar button, never takes focus, always on top
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000080 | 0x00000008)
        self._show(time.perf_counter())
        self.top.deiconify()
        self.top.after(12, self._tick)

    def _show(self, now):
        photo = self._ImageTk.PhotoImage(self.renderer.render(self.anim.state(now)))
        self.label.configure(image=photo)
        self.label.image = photo

    def _tick(self):
        if self.closed:
            return
        now = time.perf_counter()
        self._show(now)
        if self.anim.finished(now):
            self.destroy()
            return
        self.top.after(12, self._tick)

    def _local(self, x, y):
        return x - self.origin[0], y - self.origin[1]

    def open(self, targets, files):
        self.files = files
        self.lay = layout(targets, self.room)
        self.renderer.set_layout(self.lay)
        self.anim.set_count(len(self.lay["leaves"]))
        self.anim.open(time.perf_counter())

    def hover_screen(self, x, y):
        if self.lay:
            self.anim.set_hover(self.renderer.hit(*self._local(x, y)))

    def release_screen(self, x, y):
        """Let go after holding: convert if over a format, otherwise stay open for a tap."""
        if not self.lay:
            return                              # still finding the file: the branches appear shortly
        idx = self.renderer.hit(*self._local(x, y))
        if idx is not None:
            self._choose(idx)

    def click_screen(self, x, y):
        idx = self.renderer.hit(*self._local(x, y)) if self.lay else None
        if idx is not None:
            self._choose(idx)
        else:
            self.cancel()

    def _choose(self, idx):
        if self.anim.choice is None:
            self.anim.choose(idx, time.perf_counter())
            self.on_choose(self.lay["leaves"][idx]["ext"], self.files)

    def cancel(self):
        if self.anim.open_t is None:                 # still charging: just vanish
            self.anim.open(time.perf_counter())
        self.anim.cancel(time.perf_counter())

    def destroy(self):
        if not self.closed:
            self.closed = True
            try:
                self.top.destroy()
            except Exception:
                pass
            self.on_closed()
