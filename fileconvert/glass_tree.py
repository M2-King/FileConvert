"""The press-and-hold branch menu, drawn with Pillow (used on Windows).

    hold:    a ring fills around the file while you keep holding
    grow:    curvy, tapered glass branches grow out of the file one after another, each tipped with
             a glass label that springs in, glints, and has a soft mirror reflection under it
    hover:   slide onto a label and it lights up blue with its branch, popping bigger
    choose:  release on it: the other branches pull back and the label flies into the file
    cancel:  release anywhere else and tap outside: everything retracts

Like glass_hud.py, the window shows a frozen capture of the screen behind it, so the glass and
the branches look like they float over your desktop.
"""
import math

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .glass_hud import THEMES, _font, clamp, ease_in_out, ease_out, spring
from .tree import LABEL_H, LABEL_W, bezier

ACCENT = (10, 132, 255)
RING_R = 40


def _ribbon(pts, w0, w1):
    """Outline of a smooth stroke that tapers from width w0 to w1 (for branch-like lines)."""
    n = len(pts)
    left, right = [], []
    for k, (x, y) in enumerate(pts):
        xa, ya = pts[max(0, k - 1)]
        xb, yb = pts[min(n - 1, k + 1)]
        tx, ty = xb - xa, yb - ya
        L = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / L, tx / L
        w = (w0 + (w1 - w0) * k / max(1, n - 1)) / 2
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    return left + right[::-1]


class TreeAnimator:
    def __init__(self, count, now, hold_left=0.35):
        self.n = count
        self.charge_t, self.hold_left = now, hold_left
        self.open_t = self.choice_t = self.cancel_t = None
        self.choice = self.hover = None
        self.hscale = [1.0] * count
        self.last = now

    def set_count(self, count):
        self.n = count
        self.hscale = [1.0] * count

    def open(self, now):
        if self.open_t is None:
            self.open_t = now

    def set_hover(self, index):
        if self.choice is None and self.cancel_t is None:
            self.hover = index

    def choose(self, index, now):
        if self.choice is None and self.cancel_t is None:
            self.choice, self.choice_t, self.hover = index, now, index

    def cancel(self, now):
        if self.cancel_t is None and self.choice is None:
            self.cancel_t = now

    def finished(self, now):
        if self.choice_t is not None:
            return now - self.choice_t > 0.52
        if self.cancel_t is not None:
            return now - self.cancel_t > 0.34
        return False

    def state(self, now):
        dt = max(0.0, min(0.05, now - self.last))
        self.last = now
        st = {"alpha": 1.0, "ring": None, "branches": [], "labels": [], "hover": self.hover}

        if self.open_t is None:
            ct = now - self.charge_t
            st["ring"] = (clamp(ct / max(0.05, self.hold_left)), ease_out(ct / 0.12), 1.0)
            return st
        ot = now - self.open_t
        if ot < 0.2:
            st["ring"] = (1.0, 1 - ease_out(ot / 0.2), 1 + 0.25 * ease_out(ot / 0.2))

        for i in range(self.n):
            target = 1.18 if i == self.hover else 1.0
            self.hscale[i] += (target - self.hscale[i]) * min(1.0, dt * 18)
            b0 = 0.02 + i * 0.035
            grow = ease_out((ot - b0) / 0.26)
            pop = spring(ot - b0 - 0.14, 0.40, 0.60)
            glint = (ot - b0 - 0.30) / 0.55
            fly = None
            if self.choice_t is not None:
                ct = now - self.choice_t
                if i == self.choice:
                    k = ease_in_out(ct / 0.42)
                    fly = k
                    grow *= 1 - k
                    pop *= 1 - 0.5 * k
                    if ct > 0.3:
                        pop *= 1 - clamp((ct - 0.3) / 0.15)
                else:
                    grow *= 1 - ease_out(ct / 0.26)
                    pop *= 1 - ease_out(ct / 0.2)
            if self.cancel_t is not None:
                k = ease_out((now - self.cancel_t) / 0.26)
                grow *= 1 - k
                pop *= 1 - k
            st["branches"].append(grow)
            st["labels"].append({"pop": max(0.0, pop), "scale": self.hscale[i], "fly": fly,
                                 "glint": glint if 0 < glint < 1 else None})
        if self.cancel_t is not None:
            st["alpha"] = 1 - ease_out((now - self.cancel_t - 0.18) / 0.16)
        return st


class TreeRenderer:
    def __init__(self, backdrop, scale, theme, lay, root):
        """backdrop: screen capture of the window area; root: held point in window pixels."""
        self.backdrop = backdrop.convert("RGB")
        self.W, self.H = self.backdrop.size
        self.s, self.root = scale, root
        self.th = THEMES[theme]
        s = scale
        blurred = ImageEnhance.Color(self.backdrop.filter(ImageFilter.GaussianBlur(9 * s))).enhance(1.6)
        self.frosted = Image.blend(blurred, Image.new("RGB", blurred.size, self.th["tint"]), self.th["tint_a"])
        bold = ["seguisb.ttf", "SegoeUI-Semibold.ttf", "Inter-SemiBold.otf", "DejaVuSans-Bold.ttf", "arialbd.ttf"]
        regular = ["segoeui.ttf", "SegoeUI.ttf", "Inter-Regular.otf", "DejaVuSans.ttf", "arial.ttf"]
        self.f_main = _font(bold, round(12.5 * s))
        self.f_cap = _font(regular, round(9 * s))
        self._branch_key = None
        self.set_layout(lay)

    def set_layout(self, lay):
        s, (rx, ry) = self.s, self.root
        self.lay = lay
        self.paths = [[(rx + x * s, ry + y * s) for x, y in bezier(leaf["path"])] for leaf in lay["leaves"]]
        self.tips = [(rx + leaf["tip"][0] * s, ry + leaf["tip"][1] * s) for leaf in lay["leaves"]]
        self.sprites = [self._label_sprites(leaf, tip) for leaf, tip in zip(lay["leaves"], self.tips)]
        self._branch_key = None

    # -- labels -------------------------------------------------------------------------------
    def _label_sprites(self, leaf, tip):
        s, th = self.s, self.th
        h = (LABEL_H + (8 if leaf["caption"] else 0)) * s
        w = LABEL_W * s
        pad = int(10 * s)
        W, H = int(w) + 2 * pad, int(h) + 2 * pad
        ss = 3

        mask = Image.new("L", (W * ss, H * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle([pad * ss, pad * ss, (pad + w) * ss, (pad + h) * ss],
                                               radius=h / 2 * ss, fill=255)
        mask = mask.resize((W, H), Image.LANCZOS)
        inner = Image.new("L", (W * ss, H * ss), 0)
        e = 1.1 * s
        ImageDraw.Draw(inner).rounded_rectangle([(pad + e) * ss, (pad + e) * ss, (pad + w - e) * ss, (pad + h - e) * ss],
                                                radius=(h / 2 - e) * ss, fill=255)
        inner = inner.resize((W, H), Image.LANCZOS)
        grad = Image.linear_gradient("L").resize((W, H))
        edge = Image.composite(Image.new("L", (W, H), 0), mask, inner)
        edge = Image.composite(grad.point(lambda v: int(th["edge_top"] + (th["edge_bottom"] - th["edge_top"]) * v / 255)),
                               Image.new("L", (W, H), 0), edge)
        sheen = Image.composite(grad.point(lambda v: int(255 * th["sheen"] * max(0.0, 1 - v / 255 / 0.5) ** 1.5)),
                                Image.new("L", (W, H), 0), mask)
        shadow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + 3 * s, pad + w, pad + h + 3 * s], radius=h / 2,
                                                 fill=int(th["shadow"] * 0.7))
        shadow = shadow.filter(ImageFilter.GaussianBlur(5 * s))

        x0, y0 = tip[0] - W / 2, tip[1] - H / 2
        zoom = 1.08                                          # lens: magnify what's behind a little
        cx, cy = tip
        glass = self.frosted.transform((W, H), Image.EXTENT,
                                       (cx - W / 2 / zoom, cy - H / 2 / zoom, cx + W / 2 / zoom, cy + H / 2 / zoom),
                                       Image.BICUBIC)

        def build(highlight):
            sp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sp.paste((0, 0, 0, 255), (0, 0), shadow)
            body = glass.copy() if not highlight else Image.new("RGB", (W, H), ACCENT)
            sp.paste(body, (0, 0), mask)
            sp.paste((255, 255, 255, 255), (0, 0), sheen if not highlight else sheen.point(lambda v: v // 2))
            sp.paste((255, 255, 255, 255), (0, 0), edge)
            d = ImageDraw.Draw(sp)
            col = (255, 255, 255) if highlight else th["title"]
            sub = (225, 235, 255) if highlight else th["sub"]
            mid = (W / 2, H / 2 - (4 * s if leaf["caption"] else 0))
            d.text(mid, leaf["label"], font=self.f_main, anchor="mm", fill=col)
            if leaf["caption"]:
                d.text((W / 2, H / 2 + 8 * s), leaf["caption"], font=self.f_cap, anchor="mm", fill=sub)
            return sp

        def reflection(sp):
            body = sp.crop((0, pad, W, int(pad + h)))
            flip = body.transpose(Image.FLIP_TOP_BOTTOM)
            fh = int(h * 0.55)
            flip = flip.resize((W, fh), Image.BICUBIC)
            fade = Image.linear_gradient("L").resize((W, fh)).point(lambda v: int((1 - v / 255) ** 1.6 * 80))
            a = Image.composite(fade, Image.new("L", (W, fh), 0), flip.getchannel("A"))
            flip.putalpha(a)
            return flip

        normal, lit = build(False), build(True)
        return {"normal": normal, "lit": lit, "refl": reflection(normal), "refl_lit": reflection(lit),
                "pad": pad, "h": h, "origin": (x0, y0)}

    # -- branches -----------------------------------------------------------------------------
    def _branches(self, progress, hover):
        key = (tuple(round(p, 3) for p in progress), hover)
        if key == self._branch_key:
            return self._branch_layer
        s = self.s
        xs = [x for pts in self.paths for x, _ in pts]
        ys = [y for pts in self.paths for _, y in pts]
        m = int(14 * s)
        bx, by = max(0, int(min(xs)) - m), max(0, int(min(ys)) - m)
        bw, bh = min(self.W, int(max(xs)) + m) - bx, min(self.H, int(max(ys)) + m) - by
        ss, q = 2, 0.5                                       # stroke supersampling, glow downsampling
        sharp = Image.new("RGBA", (bw * ss, bh * ss), (0, 0, 0, 0))
        soft = Image.new("RGBA", (int(bw * q) + 1, int(bh * q) + 1), (0, 0, 0, 0))
        d, g = ImageDraw.Draw(sharp), ImageDraw.Draw(soft)
        for i, (pts, p) in enumerate(zip(self.paths, progress)):
            if p <= 0.01:
                continue
            n = max(2, int(round(len(pts) * p)))
            seg = pts[:n]
            lit = i == hover
            col = ACCENT + (255,) if lit else (255, 255, 255, 238)
            w0, w1 = 3.8 * s, 3.8 * s - 2.2 * s * (n - 1) / (len(pts) - 1)
            local = [((x - bx) * ss, (y - by) * ss) for x, y in seg]
            d.polygon(_ribbon(local, w0 * ss, w1 * ss), fill=col)
            tx, ty = local[-1]
            d.ellipse([tx - w1 * ss / 2, ty - w1 * ss / 2, tx + w1 * ss / 2, ty + w1 * ss / 2], fill=col)
            glow_pts = [((x - bx) * q, (y - by) * q) for x, y in seg]
            g.polygon(_ribbon(glow_pts, 9 * s * q, 6 * s * q),
                      fill=(ACCENT + (110,)) if lit else (255, 255, 255, 75))
        soft = soft.filter(ImageFilter.GaussianBlur(3.5 * s * q)).resize((bw, bh), Image.BILINEAR)
        soft.alpha_composite(sharp.resize((bw, bh), Image.LANCZOS))
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        layer.paste(soft, (bx, by))
        self._branch_layer, self._branch_key = layer, key
        return layer

    def _ring(self, progress, alpha, scale):
        ss, s = 4, self.s
        R = RING_R * s * scale
        D = int(2 * R + 8 * s) * ss // ss
        img = Image.new("RGBA", (D * ss, D * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c = D * ss / 2
        lw = int(3.4 * s * ss)
        a = int(255 * alpha)
        d.ellipse([c - R * ss, c - R * ss, c + R * ss, c + R * ss], outline=(255, 255, 255, int(70 * alpha)), width=lw)
        if progress > 0.01:
            d.arc([c - R * ss, c - R * ss, c + R * ss, c + R * ss], -90, -90 + 360 * progress,
                  fill=(255, 255, 255, a), width=lw)
        return img.resize((D, D), Image.LANCZOS)

    # -- frame --------------------------------------------------------------------------------
    def render(self, st):
        frame = self.backdrop.copy().convert("RGBA")
        if st["ring"]:
            ring = self._ring(*st["ring"])
            frame.alpha_composite(ring, (int(self.root[0] - ring.width / 2), int(self.root[1] - ring.height / 2)))
        if st["branches"]:
            frame.alpha_composite(self._branches(st["branches"], st["hover"]))
        for i, lab in enumerate(st["labels"]):
            pop = lab["pop"]
            if pop <= 0.01:
                continue
            sp = self.sprites[i]
            lit = i == st["hover"]
            img, refl = (sp["lit"], sp["refl_lit"]) if lit else (sp["normal"], sp["refl"])
            k = pop * lab["scale"]
            cx, cy = self.tips[i]
            if lab["fly"] is not None:
                cx += (self.root[0] - cx) * lab["fly"]
                cy += (self.root[1] - cy) * lab["fly"]
            if lab["glint"] is not None and not lit:
                img = self._glint(img, lab["glint"], sp)
            w, h = max(1, int(img.width * k)), max(1, int(img.height * k))
            im = img.resize((w, h), Image.BILINEAR)
            if pop < 1:
                im.putalpha(im.getchannel("A").point(lambda v: int(v * min(1.0, pop * 1.4))))
            if lab["fly"] is None:
                rw, rh = max(1, int(refl.width * k)), max(1, int(refl.height * k))
                rf = refl.resize((rw, rh), Image.BILINEAR)
                frame.alpha_composite(rf, (int(cx - rw / 2), int(cy + sp["h"] * k / 2 + 2 * self.s)))
            frame.alpha_composite(im, (int(cx - w / 2), int(cy - h / 2)))
        out = frame.convert("RGB")
        if st["alpha"] < 0.998:
            out = Image.blend(self.backdrop, out, max(0.0, st["alpha"]))
        return out

    def _glint(self, img, t, sp):
        w, h = img.size
        band = Image.new("L", (w, h), 0)
        x = -20 * self.s + (w + 40 * self.s) * ease_in_out(t)
        ImageDraw.Draw(band).polygon([(x, 0), (x + 10 * self.s, 0), (x - 6 * self.s, h), (x - 16 * self.s, h)], fill=150)
        band = band.filter(ImageFilter.GaussianBlur(3 * self.s))
        band = Image.composite(band, Image.new("L", (w, h), 0), img.getchannel("A").point(lambda v: 255 if v > 200 else 0))
        out = img.copy()
        out.paste((255, 255, 255, 255), (0, 0), band)
        return out

    def hit(self, x, y):
        """Label index under window pixel (x, y)."""
        from .tree import hit_test
        return hit_test(self.lay, (x - self.root[0]) / self.s, (y - self.root[1]) / self.s)
