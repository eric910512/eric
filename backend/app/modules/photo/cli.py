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
