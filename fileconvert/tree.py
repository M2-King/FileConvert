"""Branch layout for the press-and-hold menu.

Curvy branches grow straight out of the file you're holding, one per format, and each ends in a
glass label. The branches fan out toward the side of the screen with the most room (right by
default, like a hand-drawn sketch).

Coordinates are in points relative to the held point; x goes right, y goes down.
The macOS helper (macos/FileConvertUI.swift) has the same layout written in Swift.
"""
import math

LABEL_W, LABEL_H = 58, 30
START_R = 36                 # branches start just outside the icon


def caption_for(label):
    """'MP3 (audio only)' -> ('MP3', 'audio')."""
    main, _, rest = label.partition(" (")
    rest = rest.rstrip(")")
    return main, {"audio only": "audio", "animated": "animated", "video": "video", "word": "Word",
                  "text": "text", "pages": "pages"}.get(rest.lower(), rest)


def fan_arc(room, n):
    """Choose where the branches go: (centre angle, spread) in degrees, 0 = right, 90 = down.
    room = free space from the held point to the screen edges: {"right", "left", "up", "down"}.
    Branches only point where their labels fit, using the biggest open arc."""
    want = 0.0 if n < 2 else min(168.0, 27.0 * (n - 1))
    if not room:
        return 15.0, want
    reach = 196.0 if n > 4 else 150.0

    def fits(deg):
        """Would a label at this angle (and its reflection) stay on screen?"""
        tx = math.cos(math.radians(deg)) * (reach + LABEL_W * 0.42)
        ty = math.sin(math.radians(deg)) * (reach + LABEL_H * 0.75)
        need = {"left": LABEL_W / 2 - tx, "right": tx + LABEL_W / 2,
                "up": LABEL_H / 2 - ty, "down": ty + LABEL_H / 2 + 22}
        return all(need[k] <= room.get(k, 999) - 6 for k in need)

    ok = [fits(a) for a in range(-180, 180, 3)]
    if all(ok):
        return 15.0, want                     # room everywhere: fan out right and a bit down
    # longest run of fitting angles on the circle
    best, start, length = (0, 0), None, 0
    doubled = ok + ok
    for i, good in enumerate(doubled):
        if good:
            if start is None:
                start = i
            length = i - start + 1
            if length > best[1] and length <= len(ok):
                best = (start, length)
        else:
            start = None
    if best[1] == 0:
        return 15.0, want
    lo = -180 + best[0] * 3
    width = (best[1] - 1) * 3
    # prefer pointing right/down-right inside the open arc (like the sketch), else its middle
    centre = min(max(15.0, lo + want / 2), lo + width - want / 2) if width >= want else lo + width / 2
    return centre, min(want, width)


def layout(targets, room=None):
    """targets: list of formats.Target. Returns {"angle": deg, "leaves": [...]} where each leaf has
    ext, label, caption, tip (label centre), path (4 cubic Bézier points from icon to label)."""
    n = len(targets)
    center, span = fan_arc(room, n)
    leaves = []
    for i, t in enumerate(targets):
        a = math.radians(center - span / 2 + (span * i / (n - 1) if n > 1 else 0))
        r = 150.0 if n <= 4 else (128.0 if i % 2 == 0 else 196.0)
        d = (math.cos(a), math.sin(a))
        p = (-d[1], d[0])                                  # perpendicular, for the curve's bend
        start = (d[0] * START_R, d[1] * START_R)
        end = (d[0] * r, d[1] * r)
        bend = (1 if i % 2 else -1) * 0.20 * r
        c1 = (start[0] + (end[0] - start[0]) * 0.35 + p[0] * bend, start[1] + (end[1] - start[1]) * 0.35 + p[1] * bend)
        c2 = (start[0] + (end[0] - start[0]) * 0.72 - p[0] * bend * 0.45, start[1] + (end[1] - start[1]) * 0.72 - p[1] * bend * 0.45)
        tip = (d[0] * (r + LABEL_W * 0.42), d[1] * (r + LABEL_H * 0.75))
        main, cap = caption_for(t.label)
        leaves.append({"ext": t.ext, "label": main, "caption": cap, "tip": tip,
                       "path": [start, c1, c2, end]})
    return {"angle": center, "leaves": leaves}


def bezier(points, steps=28):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    out = []
    for k in range(steps + 1):
        t = k / steps
        u = 1 - t
        out.append((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                    u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3))
    return out


def bounds(lay, pad=24):
    xs, ys = [0.0], [0.0]
    for leaf in lay["leaves"]:
        xs += [leaf["tip"][0] - LABEL_W / 2 - pad, leaf["tip"][0] + LABEL_W / 2 + pad]
        ys += [leaf["tip"][1] - LABEL_H / 2 - pad, leaf["tip"][1] + LABEL_H + 14 + pad]   # + reflection
        for x, y in leaf["path"]:
            xs.append(x)
            ys.append(y)
    return min(xs) - 50, min(ys) - 50, max(xs) + 50, max(ys) + 50


def hit_test(lay, x, y, grow=1.0):
    """Index of the label under (x, y) relative to the held point, or None."""
    best = None
    for i, leaf in enumerate(lay["leaves"]):
        tx, ty = leaf["tip"]
        if abs(x - tx) <= LABEL_W / 2 + 6 and abs(y - ty) <= LABEL_H / 2 + 6:
            dist = math.hypot(x - tx, y - ty)
            if best is None or dist < best[0]:
                best = (dist, i)
    return None if best is None else best[1]
