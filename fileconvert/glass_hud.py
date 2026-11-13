"""Frosted-glass "island" shown at the top of the screen while converting (used on Windows).

Everything is drawn with Pillow (antialiased, same look on every PC); Tk only puts the picture on
screen. The glass is real: we capture the screen area behind the island, blur and tint it, then
draw a polished pill on top with a specular edge, a moving shine, a gradient progress ring and
spring animations. Because the window shows the captured pixels around the pill, it looks
see-through without any special Windows APIs.

    appear:   drop in as a circle -> spring open into a pill
    working:  gradient ring fills with real progress, shine sweeps across the glass
    done:     ring pops into a green check that draws itself, then the pill folds away
    failed:   red cross + a little shake; message stays readable for a few seconds
"""
import math
import os
import queue
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

PILL_W, PILL_H = 340, 64                  # logical px
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 30, 12, 40
WIN_W, WIN_H = PILL_W + 2 * MARGIN_X, PILL_H + MARGIN_TOP + MARGIN_BOTTOM
RING = 34
PAD = 15

GREEN = (52, 199, 89)
RED = (255, 69, 58)
CONIC = [(100, 210, 255), (10, 132, 255), (191, 90, 242), (255, 55, 95), (100, 210, 255)]

THEMES = {
    "light": dict(tint=(250, 250, 252), tint_a=0.34, sat=1.6, blur=13, title=(18, 18, 22), sub=(72, 72, 80),
                  track=(0, 0, 0, 34), sheen=0.30, edge_top=235, edge_bottom=70, shadow=95, shine=0.30),
    "dark": dict(tint=(28, 28, 32), tint_a=0.44, sat=1.5, blur=13, title=(246, 246, 248), sub=(174, 174, 182),
                 track=(255, 255, 255, 40), sheen=0.13, edge_top=150, edge_bottom=30, shadow=120, shine=0.16),
}


# ----------------------------------------------------------------------------- easing

def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def spring(t, response=0.42, damping=0.75):
    """Damped spring from 0 to 1 (overshoots a little when damping < 1), like SwiftUI's."""
    if t <= 0:
        return 0.0
    w0 = 2 * math.pi / response
    if damping >= 1:
        return 1 - math.exp(-w0 * t) * (1 + w0 * t)
    wd = w0 * math.sqrt(1 - damping ** 2)
    return 1 - math.exp(-damping * w0 * t) * (math.cos(wd * t) + damping * w0 / wd * math.sin(wd * t))


# ----------------------------------------------------------------------------- animation timeline

class Animator:
    """Turns events (progress, status, finish) into a per-frame visual state."""

    def __init__(self, target_label, now):
        self.t0 = self.last = now
        self.label = target_label
        self.status = "Starting…"
        self.target = None            # None = unknown progress (spinner)
        self.shown = 0.0
        self.finished_at = self.outro_at = None
        self.ok, self.message, self.path = None, "", None

    def on_progress(self, fraction):
        self.target = None if fraction is None else clamp(fraction)

    def on_status(self, text):
        self.status = text

    def on_finish(self, ok, message, path, now):
        if self.finished_at is None:
            self.finished_at = now
            self.ok, self.message, self.path = ok, message, path
            self.outro_at = now + (2.3 if ok else 4.5)

    def dismiss(self, now):
        if self.finished_at is not None and self.outro_at > now + 0.1:
            self.outro_at = now

    def ended(self, now):
        return self.outro_at is not None and now >= self.outro_at + 0.62

    def state(self, now):
        t, dt = now - self.t0, max(0.0, now - self.last)
        self.last = now

        alpha = ease_out(t / 0.18)
        pop = 0.55 + 0.45 * spring(t, 0.38, 0.62)
        width = PILL_H + (PILL_W - PILL_H) * spring(t - 0.12, 0.52, 0.72)
        content = clamp((t - 0.30) / 0.22)
        text_alpha, ox, oy = 1.0, 0.0, 0.0

        goal = 1.0 if self.ok else (self.target if self.target is not None else self.shown)
        self.shown += (goal - self.shown) * min(1.0, dt * (14 if self.ok else 7))

        spinning = self.target is None and self.finished_at is None
        ring = {"kind": "spin" if spinning else "progress", "value": self.shown,
                "angle": (now * 420) % 360, "pop": 1.0, "draw": 0.0}
        title = f"Converting to {self.label}"
        sub = self.status
        pct = None if spinning or self.finished_at else f"{round(self.shown * 100)}%"

        shine = None
        if self.finished_at is None and t > 0.9:
            phase = (t - 0.9) % 2.8
            if phase < 1.1:
                shine = -0.3 + 1.6 * ease_in_out(phase / 1.1)

        if self.finished_at is not None:
            ft = now - self.finished_at
            settle = 0.18 if self.ok else 0.0
            ring = {"kind": "done" if self.ok else "fail", "value": 1.0, "angle": 0,
                    "pop": spring(ft - settle, 0.42, 0.55), "draw": ease_out((ft - settle - 0.12) / 0.34)}
            if ft < settle:                       # let the ring finish filling first
                ring = {"kind": "progress", "value": self.shown, "angle": 0, "pop": 1.0, "draw": 0.0}
            text_alpha = abs(1 - 2 * clamp(ft / 0.26))   # quick cross-fade of the text
            if ft >= 0.13:
                title = self.message if self.ok else "Couldn't convert"
                sub = ("Click to show in folder" if self.path else "") if self.ok else self.message
            if not self.ok and ft < 0.9:
                ox = 9 * math.sin(2 * math.pi * 7 * ft) * math.exp(-5.5 * ft)

        if self.outro_at is not None and now >= self.outro_at:
            ot = now - self.outro_at
            content *= 1 - clamp(ot / 0.14)
            width = PILL_W - (PILL_W - PILL_H) * spring(ot - 0.04, 0.36, 0.86)
            k = ease_out((ot - 0.30) / 0.30) if ot > 0.30 else 0.0
            pop *= 1 - 0.45 * k
            alpha *= 1 - k
            oy = -14 * k

        return {"alpha": alpha, "pop": pop, "width": width, "content": content, "text": text_alpha,
                "ox": ox, "oy": oy, "ring": ring, "title": title, "sub": sub, "pct": pct, "shine": shine}


# ----------------------------------------------------------------------------- drawing

def _font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _conic(size):
    """Angular gradient used for the progress ring (pure Python, made once)."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    c = (size - 1) / 2
    n = len(CONIC) - 1
    for y in range(size):
        for x in range(size):
            a = (math.degrees(math.atan2(y - c, x - c)) + 90) % 360 / 360 * n
            i, f = int(a), a - int(a)
            c0, c1 = CONIC[i], CONIC[min(i + 1, n)]
            px[x, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
    return img


def _trim(points, t):
    """First t (0..1) of a polyline, for 'drawing' check marks."""
    segs = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    lengths = [math.dist(a, b) for a, b in segs]
    left = sum(lengths) * clamp(t)
    out = [points[0]]
    for (a, b), L in zip(segs, lengths):
        if left <= 0:
            break
        f = min(1.0, left / L) if L else 1.0
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        left -= L
    return out


def _stroke(draw, pts, width, fill):
    if len(pts) < 2:
        return
    draw.line(pts, fill=fill, width=int(round(width)), joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


class GlassRenderer:
    def __init__(self, backdrop, scale=1.0, theme="light"):
        self.backdrop = backdrop.convert("RGB")
        self.W, self.H = self.backdrop.size
        self.s = scale
        self.th = THEMES[theme]
        th, s = self.th, scale
        blurred = self.backdrop.filter(ImageFilter.GaussianBlur(th["blur"] * s))
        blurred = ImageEnhance.Color(blurred).enhance(th["sat"])
        self.frosted = Image.blend(blurred, Image.new("RGB", blurred.size, th["tint"]), th["tint_a"])
        bold = ["seguisb.ttf", "SegoeUI-Semibold.ttf", "Inter-SemiBold.otf", "DejaVuSans-Bold.ttf", "arialbd.ttf"]
        regular = ["segoeui.ttf", "SegoeUI.ttf", "Inter-Regular.otf", "DejaVuSans.ttf", "arial.ttf"]
        self.f_title = _font(bold, round(14 * s))
        self.f_sub = _font(regular, round(12 * s))
        self.f_pct = _font(bold, round(12.5 * s))
        self.conic = _conic(int(RING * s * 4))
        band = Image.linear_gradient("L").rotate(90, expand=True).resize((int(70 * s), 8))
        band = band.point(lambda v: int(255 * math.sin(math.pi * v / 255) ** 2))
        self.band = band.resize((int(70 * s), int(PILL_H * s * 3))).rotate(-18, expand=True,
                                                                           resample=Image.BICUBIC)
        self._geo_key, self._geo = None, None

    # Geometry-only layers (mask, shadow, sheen, edge) are cached while the pill isn't moving.
    def _layers(self, x0, y0, w, h):
        key = (round(x0 * 4), round(y0 * 4), round(w * 4), round(h * 4))
        if key == self._geo_key:
            return self._geo
        ss, s, th = 3, self.s, self.th
        bx, by = math.floor(x0) - 1, math.floor(y0) - 1
        bw, bh = math.ceil(x0 + w) - bx + 2, math.ceil(y0 + h) - by + 2
        fx, fy = (x0 - bx) * ss, (y0 - by) * ss
        r = h / 2 * ss

        def pill(inset=0.0):
            m = Image.new("L", (bw * ss, bh * ss), 0)
            ImageDraw.Draw(m).rounded_rectangle(
                [fx + inset * ss, fy + inset * ss, fx + w * ss - inset * ss, fy + h * ss - inset * ss],
                radius=max(1, r - inset * ss), fill=255)
            return m.resize((bw, bh), Image.LANCZOS)

        body = pill()
        inner = pill(1.15 * s)
        edge = Image.composite(Image.new("L", body.size, 0), body, inner)       # ring = body - inner
        grad = Image.linear_gradient("L").resize((1, 256)).resize((bw, bh))     # 0 top -> 255 bottom
        top, bottom = th["edge_top"], th["edge_bottom"]
        edge_alpha = Image.eval(grad, lambda v: int(top + (bottom - top) * v / 255))
        edge = Image.composite(edge_alpha, Image.new("L", body.size, 0), edge)

        sheen_g = grad.point(lambda v: int(255 * th["sheen"] * max(0.0, 1 - v / 255 / 0.55) ** 1.6))
        sheen = Image.composite(sheen_g, Image.new("L", body.size, 0), body)

        # soft shadow, drawn small and blurred for speed
        q = 4
        sp = int(28 * s)
        sw, sh = (bw + 2 * sp) // q + 1, (bh + 2 * sp) // q + 1
        shadow = Image.new("L", (sw, sh), 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            [(sp + x0 - bx) / q, (sp + y0 - by + 9 * s) / q, (sp + x0 - bx + w) / q, (sp + y0 - by + h + 9 * s) / q],
            radius=h / 2 / q, fill=th["shadow"])
        shadow = shadow.filter(ImageFilter.GaussianBlur(14 * s / q)).resize((sw * q, sh * q), Image.BILINEAR)

        # liquid-glass lens: what's behind is magnified a little, so edges bend the background
        zoom = 1.06
        cxl, cyl = bx + bw / 2, by + bh / 2
        src = (cxl - bw / 2 / zoom, cyl - bh / 2 / zoom, cxl + bw / 2 / zoom, cyl + bh / 2 / zoom)
        glass = self.frosted.transform((bw, bh), Image.EXTENT, src, Image.BICUBIC)

        self._geo_key = key
        self._geo = dict(box=(bx, by), size=(bw, bh), body=body, edge=edge, sheen=sheen, glass=glass,
                         shadow=shadow, shadow_box=(bx - sp, by - sp))
        return self._geo

    def _ring(self, d, ring):
        ss = 4
        D = max(8, int(round(d * ss)))
        lw = 3.7 * self.s * ss * d / (RING * self.s)
        layer = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        c = D / 2
        R = D / 2 - 1
        kind = ring["kind"]
        if kind in ("progress", "spin"):
            dr.ellipse([c - R, c - R, c + R, c + R], outline=self.th["track"], width=int(lw))
            if kind == "progress":
                start, extent = -90.0, 360.0 * clamp(ring["value"])
            else:
                start, extent = ring["angle"] - 90, 105.0
            if extent > 0.6:
                mask = Image.new("L", (D, D), 0)
                md = ImageDraw.Draw(mask)
                md.arc([c - R, c - R, c + R, c + R], start, start + extent, fill=255, width=int(lw))
                rc = R - lw / 2
                for a in (start, start + extent):
                    x, y = c + rc * math.cos(math.radians(a)), c + rc * math.sin(math.radians(a))
                    md.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=255)
                grad = self.conic.resize((D, D))
                if kind == "spin":
                    grad = grad.rotate(-ring["angle"])
                layer.paste(grad, mask=mask)
        else:
            col = GREEN if kind == "done" else RED
            base = R * 0.88                       # room for the springy overshoot
            rr = min(R, base * max(0.0, ring["pop"]))
            dr.ellipse([c - rr, c - rr, c + rr, c + rr], fill=col + (255,))
            u = lambda px, py: (c - base + 2 * base * px, c - base + 2 * base * py)  # noqa: E731
            if kind == "done":
                _stroke(dr, _trim([u(.29, .52), u(.44, .67), u(.72, .36)], ring["draw"]), lw * 1.05, "white")
            else:
                _stroke(dr, _trim([u(.35, .35), u(.65, .65)], ring["draw"] * 2), lw, "white")
                _stroke(dr, _trim([u(.65, .35), u(.35, .65)], ring["draw"] * 2 - 1), lw, "white")
        return layer.resize((max(1, round(d)), max(1, round(d))), Image.LANCZOS)

    def _ellipsize(self, draw, text, font, max_w):
        if draw.textlength(text, font=font) <= max_w:
            return text
        while text and draw.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text + "…"

    def render(self, st):
        frame = self.backdrop.copy()
        if st["alpha"] <= 0.002:
            return frame
        s, th = self.s, self.th
        w = st["width"] * s * st["pop"]
        h = PILL_H * s * st["pop"]
        cx = self.W / 2 + st["ox"] * s
        cy = (MARGIN_TOP + PILL_H / 2 + st["oy"]) * s
        x0, y0 = cx - w / 2, cy - h / 2
        L = self._layers(x0, y0, w, h)
        bx, by = L["box"]

        frame.paste((0, 0, 0), L["shadow_box"], L["shadow"])
        frame.paste(L["glass"], (bx, by), L["body"])
        frame.paste((255, 255, 255), (bx, by), L["sheen"])

        if st["shine"] is not None:
            m = Image.new("L", L["size"], 0)
            px = int((x0 - bx) + w * st["shine"] - self.band.width / 2)
            m.paste(self.band, (px, int(h / 2 - self.band.height / 2)))
            m = Image.composite(m.point(lambda v: int(v * th["shine"])), Image.new("L", m.size, 0), L["body"])
            frame.paste((255, 255, 255), (bx, by), m)

        frame.paste((255, 255, 255), (bx, by), L["edge"])

        if st["content"] > 0.01:
            k = st["pop"]
            layer = Image.new("RGBA", L["size"], (0, 0, 0, 0))
            d = RING * s * k
            ring = self._ring(d, st["ring"])
            rx = (x0 - bx) + PAD * s * k
            ry = (y0 - by) + h / 2 - d / 2
            layer.alpha_composite(ring, (int(round(rx)), int(round(ry))))

            ta = st["text"]
            if ta > 0.01:
                dr = ImageDraw.Draw(layer)
                tx = rx + d + 12 * s * k
                right = (x0 - bx) + w - (PAD + 4) * s * k
                if st["pct"]:
                    dr.text((right, (y0 - by) + h / 2), st["pct"], font=self.f_pct, anchor="rm",
                            fill=th["sub"] + (int(255 * ta),))
                    right -= dr.textlength("100%", font=self.f_pct) + 10 * s
                avail = max(10, right - tx)
                title = self._ellipsize(dr, st["title"], self.f_title, avail)
                if st["sub"]:
                    sub = self._ellipsize(dr, st["sub"], self.f_sub, avail)
                    dr.text((tx, (y0 - by) + h / 2 - 1.5 * s), title, font=self.f_title, anchor="ls",
                            fill=th["title"] + (int(255 * ta),))
                    dr.text((tx, (y0 - by) + h / 2 + 3 * s), sub, font=self.f_sub, anchor="lt",
                            fill=th["sub"] + (int(255 * ta),))
                else:
                    dr.text((tx, (y0 - by) + h / 2), title, font=self.f_title, anchor="lm",
                            fill=th["title"] + (int(255 * ta),))
            a = layer.getchannel("A")
            a = Image.composite(a.point(lambda v: int(v * st["content"])), Image.new("L", a.size, 0), L["body"])
            frame.paste(layer.convert("RGB"), (bx, by), a)

        if st["alpha"] < 0.998:
            frame = Image.blend(self.backdrop, frame, st["alpha"])
        return frame


# ----------------------------------------------------------------------------- Windows window

class _QueueReporter:
    """Reporter used by the conversion thread; the HUD drains the queue every frame."""

    def __init__(self, q):
        self.q = q

    def progress(self, fraction):
        self.q.put(("progress", fraction))

    def status(self, text):
        self.q.put(("status", text))

    def finish(self, ok, message, path=None):
        self.q.put(("finish", ok, message, path))


def _windows_theme():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def _claim_slot():
    """Several files converting at once (multi-select) stack their islands instead of overlapping."""
    from .system import data_dir
    folder = data_dir() / "hud"
    folder.mkdir(exist_ok=True)
    for i in range(8):
        f = open(folder / f"slot{i}.lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return i, f                      # the lock is released when the process exits
        except OSError:
            f.close()
    return 0, None


def run_with_hud(job, target_label):
    """Run job(reporter) on a worker thread while the glass island animates. Returns job's result."""
    import ctypes
    import tkinter as tk
    from PIL import ImageGrab, ImageTk

    scale = 1.0
    left, top, right = 0, 0, None
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            scale = ctypes.windll.user32.GetDpiForSystem() / 96
        except AttributeError:
            pass
        rect = (ctypes.c_long * 4)()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, rect, 0)     # work area (minus taskbar)
        left, top, right = rect[0], rect[1], rect[2]
    theme = _windows_theme() if sys.platform == "win32" else "light"

    slot, _slot_lock = _claim_slot()
    W, H = int(WIN_W * scale), int(WIN_H * scale)
    root = tk.Tk()
    root.withdraw()
    if right is None:
        right = root.winfo_screenwidth()
    x = left + (right - left - W) // 2
    y = top + int(4 * scale) + slot * (H - int(MARGIN_BOTTOM * scale * 0.55))
    backdrop = ImageGrab.grab(bbox=(x, y, x + W, y + H))
    renderer = GlassRenderer(backdrop, scale, theme)

    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{W}x{H}+{x}+{y}")
    label = tk.Label(root, bd=0, highlightthickness=0, cursor="hand2")
    label.pack()
    if sys.platform == "win32":
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        # no taskbar button, never steals focus from what you're doing
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000080 | 0x00000008)

    q = queue.Queue()
    anim = Animator(target_label, time.perf_counter())
    result = {}

    def worker():
        try:
            result["rc"] = job(_QueueReporter(q))
        except Exception as e:          # should not happen (convert_many catches), but never hang
            q.put(("finish", False, f"{type(e).__name__}: {e}", None))
            result["rc"] = 1

    def on_click(_event):
        now = time.perf_counter()
        if anim.ok and anim.path:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(anim.path)])
            anim.dismiss(now)
        elif anim.finished_at is not None:
            anim.dismiss(now)

    label.bind("<Button-1>", on_click)
    thread = threading.Thread(target=worker, daemon=True)

    def tick():
        now = time.perf_counter()
        while True:
            try:
                ev = q.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "progress":
                anim.on_progress(ev[1])
            elif ev[0] == "status":
                anim.on_status(ev[1])
            else:
                anim.on_finish(ev[1], ev[2], ev[3], now)
        if not thread.is_alive() and anim.finished_at is None and q.empty():
            anim.on_finish(False, "Stopped unexpectedly", None, now)
        photo = ImageTk.PhotoImage(renderer.render(anim.state(now)))
        label.configure(image=photo)
        label.image = photo
        if anim.ended(now):
            root.destroy()
            return
        root.after(12, tick)

    photo = ImageTk.PhotoImage(renderer.render(anim.state(time.perf_counter())))
    label.configure(image=photo)
    label.image = photo
    root.deiconify()
    thread.start()
    root.after(12, tick)
    root.mainloop()
    thread.join(timeout=1)
    return result.get("rc", 1)
