"""The conversion table: which formats each file type can become, and which engine does the work.

Engines:
    pillow  - images (pure Python, always bundled)
    heif    - lets Pillow read iPhone HEIC/HEIF photos (pillow-heif)
    ffmpeg  - audio and video
    soffice - LibreOffice, for Word / Excel / PowerPoint style documents
    pdf     - PDF to Word, text and page images (pypdfium2 + pdf2docx, bundled)
"""
from collections import namedtuple

Target = namedtuple("Target", "ext engine label")

# Different spellings of the same format.
ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff",
    "oga": "ogg", "mpeg": "mpg",
}

# category: (engine, input extensions, output formats in menu order)
CATEGORIES = {
    "image":  ("pillow",  ["png", "jpg", "jpeg", "jpe", "webp", "bmp", "gif", "tif", "tiff",
                           "ico", "heic", "heif"],
                          ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "pdf"]),
    "audio":  ("ffmpeg",  ["mp3", "wav", "flac", "aac", "m4a", "ogg", "oga", "opus", "wma",
                           "aif", "aiff", "amr"],
                          ["mp3", "wav", "flac", "m4a", "ogg", "opus", "aiff"]),
    "video":  ("ffmpeg",  ["mp4", "mov", "mkv", "avi", "webm", "wmv", "flv", "m4v", "mpg",
                           "mpeg", "3gp", "ts", "mts", "m2ts"],
                          ["mp4", "mov", "mkv", "webm", "avi", "gif", "mp3", "wav", "m4a"]),
    "text":   ("soffice", ["doc", "docx", "odt", "rtf", "txt"],
                          ["pdf", "docx", "odt", "rtf", "txt"]),
    "sheet":  ("soffice", ["xls", "xlsx", "ods", "csv"],
                          ["pdf", "xlsx", "ods", "csv"]),
    "slides": ("soffice", ["ppt", "pptx", "odp"],
                          ["pdf", "pptx", "odp"]),
    "pdf":    ("pdf",     ["pdf"],
                          ["docx", "txt", "png", "jpg"]),
}

# Extra cross-category conversions for specific inputs.
EXTRAS = {
    "gif": [("mp4", "ffmpeg"), ("webm", "ffmpeg")],   # animated GIF -> video
}

AUDIO_FORMATS = set(CATEGORIES["audio"][2])


def canonical(ext):
    ext = ext.lower().lstrip(".")
    return ALIASES.get(ext, ext)


def category_of(ext):
    ext = ext.lower().lstrip(".")
    for name, (_, inputs, _) in CATEGORIES.items():
        if ext in inputs:
            return name
    return None


def _label(category, target):
    if category == "video" and target in AUDIO_FORMATS:
        return f"{target.upper()} (audio only)"
    if category == "video" and target == "gif":
        return "GIF (animated)"
    if category == "image" and target in ("mp4", "webm"):
        return f"{target.upper()} (video)"
    if category == "pdf":
        return {"docx": "DOCX (Word)", "txt": "TXT (text)", "png": "PNG (pages)", "jpg": "JPG (pages)"}[target]
    return target.upper()


def targets_for(ext, engines):
    """Formats a file with this extension can be converted into, given the installed engines."""
    raw = ext.lower().lstrip(".")
    src = canonical(raw)
    category = category_of(raw)
    if category is None:
        return []
    engine, _, outputs = CATEGORIES[category]
    if engine not in engines:
        return []
    if src == "heic" and "heif" not in engines:
        return []
    result = [Target(t, engine, _label(category, t)) for t in outputs if t != src]
    for t, eng in EXTRAS.get(src, []):
        if eng in engines:
            result.append(Target(t, eng, _label(category, t)))
    return result


def all_input_extensions():
    seen = []
    for _, inputs, _ in CATEGORIES.values():
        seen.extend(e for e in inputs if e not in seen)
    return seen


def supported_extensions(engines):
    """Every extension that gets a right-click menu, mapped to its targets."""
    plan = {}
    for ext in all_input_extensions():
        t = targets_for(ext, engines)
        if t:
            plan[ext] = t
    return plan


def common_targets(exts, engines):
    """Targets that every one of the given extensions can be converted into (for multi-select)."""
    exts = [canonical(e) for e in exts]
    lists = [targets_for(e, engines) for e in exts]
    if not lists or any(not l for l in lists):
        return []
    # A file that's already in the target format is fine (it's just skipped), as long as at
    # least one selected file actually needs converting.
    candidates = {}
    for l in lists:
        for t in l:
            candidates.setdefault(t.ext, t)
    cats = {category_of(e) for e in exts}
    order = (list(CATEGORIES[cats.pop()][2]) if len(cats) == 1 else []) + [t.ext for l in lists for t in l]
    result = []
    for ext in sorted(candidates, key=order.index):
        ok = all(src == ext or any(o.ext == ext for o in l) for src, l in zip(exts, lists))
        if ok and any(src != ext for src in exts):
            result.append(candidates[ext])
    return result
