import click
from flask import current_app
from flask.cli import AppGroup

photo_cli = AppGroup("photos", help="皮膚照片 maintenance (private storage).")


def _storage_or_fail():
    from app.services.photo_storage import photo_feature

    if not photo_feature(current_app).view_enabled:
        raise click.ClickException("No usable photo storage is configured; nothing to do.")


@photo_cli.command("purge-pending")
def purge_pending():
    """Discard pending photos never attached to a report (older than PHOTO_PENDING_TTL_HOURS).
    Safe to run again at any time: attached photos are never touched."""
    from app.modules.photo.services import purge_expired_pending

    _storage_or_fail()
    total = 0
    while True:
        n = purge_expired_pending(limit=100)  # commits each batch, then removes the files
        total += n
        if n == 0:
            break
    click.echo(f"Discarded {total} expired pending photo(s).")


@photo_cli.command("verify")
@click.option("--fix", is_flag=True, help="Remove files of discarded photos and (local storage) files without a row.")
def verify(fix):
    """Photo rows ↔ stored files. Missing files of pending / attached photos are reported (exit code 1),
    never fixed automatically."""
    from app.modules.photo.services import verify_storage

    _storage_or_fail()
    report = verify_storage(fix=fix)
    click.echo(f"Checked {report['checked']} photo row(s): {len(report['missing'])} missing file(s), "
               f"{report['leftovers']} leftover file(s) of discarded photos, {report['orphans']} file(s) without a row"
               + (" — leftovers / orphans removed." if fix and (report['leftovers'] or report['orphans']) else "."))
    for m in report["missing"]:
        click.echo(f"  missing: photo {m['photo']} ({m['status']}, {m['variant']})")
    if report["missing"]:
        raise SystemExit(1)


def _mib(n):
    return "n/a" if n is None else f"{n / (1024 * 1024):.1f} MiB"


@photo_cli.command("storage-report")
def storage_report():
    """Database photo storage: stored image bytes against PHOTO_DB_QUOTA_BYTES, the image table's size (with
    TOAST) and the whole database's size. Counts and sizes only — no patient, photo id or key. Exit code:
    0 = ok, 1 = warning (PHOTO_DB_WARN_PERCENT), 2 = critical (PHOTO_DB_CRITICAL_PERCENT) — usable by a cron job."""
    from app.services.photo_storage import DatabasePhotoStorage, photo_feature

    storage = photo_feature(current_app).storage
    if not isinstance(storage, DatabasePhotoStorage):
        raise click.ClickException("Photo storage is not the database backend (PHOTO_STORAGE_BACKEND=database); nothing to report.")
    r = storage.usage_report()
    for variant in ("full", "thumb"):
        v = r["by_variant"].get(variant, {"images": 0, "bytes": 0})
        click.echo(f"{variant:>5} images: {v['images']:6d}  {_mib(v['bytes'])}")
    click.echo(f"photo bytes:     {_mib(r['photo_bytes'])} of quota {_mib(r['quota_bytes'])} ({r['quota_percent']}%)")
    click.echo(f"image table:     {_mib(r['table_bytes'])} (symptom_photo_blobs incl. TOAST and indexes)")
    click.echo(f"database total:  {_mib(r['database_bytes'])}"
               + (f" of disk {_mib(r['disk_bytes'])} ({r['disk_percent']}%)" if r["disk_percent"] is not None else ""))
    click.echo(f"level:           {r['level']} (warning ≥ {storage.warn_percent}%, critical ≥ {storage.critical_percent}% of quota)")
    raise SystemExit({"ok": 0, "warning": 1, "critical": 2}[r["level"]])
