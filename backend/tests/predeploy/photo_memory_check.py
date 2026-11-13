"""Photo upload MEMORY check — peak memory of the image pipeline (prepare_upload: decode, EXIF orientation,
resize, re-encode) per gunicorn worker, against a Render Free instance (512 MB). Synthetic images only; no
database, no network. Each scenario runs in a FRESH process (peak memory cannot be reset inside one).

    python tests/predeploy/photo_memory_check.py            # from backend/
    BUDGET_MB=512 WORKERS=2 THREADS=4  (defaults: render.yaml / gunicorn.conf.py)

Scenarios
  idle        the Flask app loaded (one worker's baseline)
  one-<fmt>   one upload of the worst accepted image (2048 × 2048, noisy) — JPEG / PNG / WebP
  bomb-<fmt>  a small file with a huge pixel count (rejected from the header, before decoding)
  burst       THREADS uploads at the same moment in one worker (the worst accepted WebP), as gunicorn's
              gthread worker would run them
  old-*       the same with the PREVIOUS settings (40 MP limit, no decode slot), for comparison

Pass: projected worst case WORKERS × (idle + worst worker burst delta) + master ≤ 80 % of BUDGET_MB.
"""
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
BUDGET_MB = int(os.environ.get("BUDGET_MB", "512"))
WORKERS = int(os.environ.get("WORKERS", "2"))
THREADS = int(os.environ.get("THREADS", "4"))
MASTER_MB = 30  # gunicorn arbiter (does not import the app: no preload)


def peak_mb():
    import psutil  # local venv only; not a runtime dependency

    mi = psutil.Process().memory_info()
    if hasattr(mi, "peak_wset"):  # Windows
        return mi.peak_wset / 1e6
    import resource  # Linux: KiB

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3


def image(fmt, w, h, noisy=True, quality=None):
    from PIL import Image

    if noisy:  # realistic content (a flat colour would compress to nothing)
        g = Image.linear_gradient("L").resize((w, h))
        im = Image.merge("RGB", (g, g.transpose(Image.FLIP_TOP_BOTTOM), g.transpose(Image.FLIP_LEFT_RIGHT)))
        im = Image.blend(im, Image.effect_noise((w, h), 40).convert("RGB"), 0.25)
    else:
        im = Image.new("RGB", (w, h), (90, 140, 60))
    buf = io.BytesIO()
    kw = {"JPEG": {"quality": quality or 90}, "PNG": {}, "WEBP": {"quality": 80} if noisy else {"lossless": True}}[fmt]
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def child(scenario, files):
    """Runs in its own process: load the app, read the prepared files, measure."""
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("FLASK_ENV", "testing")
    from app import create_app
    from app.modules.photo import services

    app = create_app("testing")
    if scenario.startswith("old-"):
        app.config.update(PHOTO_MAX_PIXELS=40_000_000, PHOTO_MAX_CONCURRENT_PROCESSING=64)
    datas = [(Path(f).read_bytes(), ctype) for f, ctype in files]
    idle = peak_mb()
    outcome = []

    def one(data, ctype):
        with app.app_context():
            try:
                services.prepare_upload(data, ctype)
                outcome.append("accepted")
            except Exception as exc:  # noqa: BLE001 — the API error is the expected result for bombs
                outcome.append(getattr(exc, "code", type(exc).__name__))

    if scenario.endswith("idle"):
        pass
    elif "burst" in scenario:
        ts = [threading.Thread(target=one, args=datas[0]) for _ in range(THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    else:
        one(*datas[0])
    print(json.dumps({"idle": idle, "peak": peak_mb(), "outcome": sorted(set(outcome))}))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="photo-mem-"))
    def f(name, data):
        (tmp / name).write_bytes(data)
        return str(tmp / name)

    side = 2048
    old_w = int(math.sqrt(40_000_000 * 4 / 3)) // 8 * 8
    files = {
        "jpeg": (f("a.jpg", image("JPEG", side, side)), "image/jpeg"),
        "png": (f("a.png", image("PNG", side, side)), "image/png"),
        "webp": (f("a.webp", image("WEBP", side, side)), "image/webp"),
        "bomb-jpeg": (f("b.jpg", image("JPEG", 8000, 8000, noisy=False)), "image/jpeg"),
        "bomb-webp": (f("b.webp", image("WEBP", 8000, 8000, noisy=False)), "image/webp"),
        "old-webp": (f("o.webp", image("WEBP", old_w, old_w * 3 // 4)), "image/webp"),
        "old-jpeg": (f("o.jpg", image("JPEG", old_w, old_w * 3 // 4, quality=40)), "image/jpeg"),  # < 10 MB
    }
    scenarios = [("idle", "jpeg"), ("one-jpeg", "jpeg"), ("one-png", "png"), ("one-webp", "webp"),
                 ("bomb-jpeg", "bomb-jpeg"), ("bomb-webp", "bomb-webp"), ("burst-webp", "webp"),
                 ("old-one-jpeg", "old-jpeg"), ("old-one-webp", "old-webp"), ("old-burst-webp", "webp")]
    rows = {}
    print(f"===== photo memory check (budget {BUDGET_MB} MB, {WORKERS} workers × {THREADS} threads)")
    for name, key in scenarios:
        path, ctype = files[key]
        p = subprocess.run([sys.executable, __file__, "--child", name, json.dumps([[path, ctype]])],
                           cwd=BACKEND, capture_output=True, text=True, timeout=600)
        line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), None)
        if line is None:
            print(f"[FAIL] {name}: no result\n{p.stderr[-800:]}")
            rows[name] = None
            continue
        r = json.loads(line)
        rows[name] = r
        size_kb = Path(path).stat().st_size // 1024
        print(f"      {name:15s} input {size_kb:6d} KB  outcome {','.join(r['outcome']) or '-':22s} "
              f"idle {r['idle']:6.0f} MB  peak {r['peak']:6.0f} MB  (+{r['peak'] - r['idle']:.0f})")
    ok = []

    def check(label, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))

    for b in ("bomb-jpeg", "bomb-webp"):
        r = rows.get(b)
        check(f"{b}: rejected from the header (PHOTO_TOO_MANY_PIXELS), decoding never started (< 20 MB)",
              r and r["outcome"] == ["PHOTO_TOO_MANY_PIXELS"] and r["peak"] - r["idle"] < 20, r and f"+{r['peak'] - r['idle']:.0f} MB")
    for k in ("one-jpeg", "one-png", "one-webp", "burst-webp"):
        check(f"{k}: accepted", rows.get(k) and rows[k]["outcome"] == ["accepted"])
    if all(rows.get(k) for k in ("idle", "burst-webp")):
        idle = rows["idle"]["peak"]
        worst_delta = max(rows[k]["peak"] - rows[k]["idle"] for k in ("one-jpeg", "one-png", "one-webp", "burst-webp"))
        projected = WORKERS * (idle + worst_delta) + MASTER_MB
        check(f"projected worst case {WORKERS} workers × ({idle:.0f} idle + {worst_delta:.0f} burst) + {MASTER_MB} master = "
              f"{projected:.0f} MB ≤ 80 % of {BUDGET_MB} MB ({0.8 * BUDGET_MB:.0f} MB)", projected <= 0.8 * BUDGET_MB)
        if rows.get("old-burst-webp"):
            old = rows["old-burst-webp"]["peak"] - rows["old-burst-webp"]["idle"]
            print(f"      for comparison: previous settings, {THREADS} simultaneous 2048² WebP uploads in one worker: +{old:.0f} MB; "
                  f"one 40 MP image: JPEG +{rows['old-one-jpeg']['peak'] - rows['old-one-jpeg']['idle']:.0f} MB, "
                  f"WebP +{rows['old-one-webp']['peak'] - rows['old-one-webp']['idle']:.0f} MB")
    print(f"{sum(ok)}/{len(ok)} checks passed — synthetic images, no database")
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2], json.loads(sys.argv[3]))
    else:
        main()
