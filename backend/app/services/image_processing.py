"""Validate and re-encode an uploaded photo (皮膚照片 Phase 1b).

- The content decides the type (magic bytes + Pillow), never the file name; the declared Content-Type
  must match it. JPEG / PNG / WebP only; HEIC / HEIF is recognised and refused with its own message.
- Size, pixel count (checked before decoding: decompression bombs), minimum side, single frame only.
- JPEGs are decoded at reduced scale (``draft``) to keep memory low on small instances.
- The image is turned upright (EXIF orientation), flattened to RGB and **re-encoded** as a new JPEG
  (full ≤ 1600 px, thumbnail ≤ 320 px) without any metadata: EXIF (GPS, device, time), XMP and ICC are
  dropped. Only the re-encoded files are stored; the upload itself is never kept.

Re-encoding removes metadata, not what the picture shows: the image itself is not anonymised.
"""

import hashlib
import io
import logging
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp")
PIL_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
TYPE_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png"}
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif", b"avis"}
STORED_TYPE = "image/jpeg"

# Pillow's DEBUG output prints EXIF values (device, GPS…): never let it reach the application log,
# whatever the log level of the deployment.
logging.getLogger("PIL").setLevel(logging.INFO)


class ImageRejected(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class ProcessedImage:
    full: bytes
    thumb: bytes
    width: int
    height: int
    checksum_sha256: str  # of the stored full image
    source_sha256: str  # of the uploaded bytes


def sniff(data):
    """Type from the first bytes: image/jpeg | image/png | image/webp | heif | None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS:
        return "heif"
    return None


def _rgb(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        background = Image.new("RGB", im.size, (255, 255, 255))
        background.paste(im, mask=im.getchannel("A"))
        return background
    return im.convert("RGB") if im.mode != "RGB" else im


def _jpeg(im, quality):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)  # no exif= / icc_profile=: no metadata
    return buf.getvalue()


def process(data, declared_type, *, max_bytes, max_pixels, min_side, full_side, thumb_side):
    if not data:
        raise ImageRejected(400, "PHOTO_EMPTY", "沒有收到照片檔案")
    if len(data) > max_bytes:
        raise ImageRejected(413, "PHOTO_TOO_LARGE", f"照片檔案太大（上限 {max_bytes // (1024 * 1024)} MB）")
    sniffed = sniff(data)
    if sniffed == "heif":
        raise ImageRejected(415, "PHOTO_HEIC_NOT_SUPPORTED",
                            "目前不支援 HEIC／HEIF 格式。請改用 JPEG：iPhone 可到「設定 > 相機 > 格式」選擇「最相容」，或從相簿重新選擇照片。")
    if sniffed is None:
        raise ImageRejected(415, "PHOTO_UNSUPPORTED_TYPE", "只支援 JPEG、PNG、WebP 照片")
    declared = TYPE_ALIASES.get((declared_type or "").split(";")[0].strip().lower(), (declared_type or "").split(";")[0].strip().lower())
    if declared not in ALLOWED_TYPES:
        raise ImageRejected(415, "PHOTO_UNSUPPORTED_TYPE", "只支援 JPEG、PNG、WebP 照片")
    if declared != sniffed:
        raise ImageRejected(415, "PHOTO_TYPE_MISMATCH", "檔案內容與宣告的格式不符")

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            im = Image.open(io.BytesIO(data), formats=[PIL_FORMAT[sniffed]])
            width, height = im.size
            if width * height > max_pixels:
                raise ImageRejected(413, "PHOTO_TOO_MANY_PIXELS", "照片解析度過高，請改用較小的照片")
            if min(width, height) < min_side:
                raise ImageRejected(422, "PHOTO_TOO_SMALL", "照片太小，請重新拍攝")
            if getattr(im, "n_frames", 1) > 1:
                raise ImageRejected(415, "PHOTO_ANIMATED_NOT_SUPPORTED", "不支援動態圖片")
            if im.format == "JPEG":
                im.draft("RGB", (full_side * 2, full_side * 2))  # decode at a reduced scale (memory)
            im.load()
            im = _rgb(ImageOps.exif_transpose(im))
        except ImageRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageRejected(413, "PHOTO_TOO_MANY_PIXELS", "照片解析度過高，請改用較小的照片") from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
            raise ImageRejected(422, "PHOTO_DECODE_FAILED", "無法讀取這張照片，檔案可能已損壞") from exc

    full = im.copy()
    full.thumbnail((full_side, full_side), Image.Resampling.LANCZOS)
    thumb = full.copy()
    thumb.thumbnail((thumb_side, thumb_side), Image.Resampling.LANCZOS)
    full_bytes = _jpeg(full, 85)
    return ProcessedImage(
        full=full_bytes, thumb=_jpeg(thumb, 80), width=full.width, height=full.height,
        checksum_sha256=hashlib.sha256(full_bytes).hexdigest(), source_sha256=hashlib.sha256(data).hexdigest(),
    )
