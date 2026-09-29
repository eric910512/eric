import os

import click
from flask import current_app
from flask.cli import AppGroup

from app.extensions import db
from app.seeds.dev import DEFAULT_DEMO_PASSWORD, seed_dev_data

seed_cli = AppGroup("seed", help="Load development seed data.")


@seed_cli.command("dev")
def seed_dev():
    """Create or refresh synthetic development data (safe to run repeatedly)."""
    # Same selector create_app() uses; app.debug is unreliable here because the flask CLI
    # resets it from FLASK_DEBUG.
    config_name = os.environ.get("FLASK_ENV", "development")
    if not current_app.config.get("ALLOW_DEMO_SEED") and not current_app.testing:
        raise click.ClickException(
            f"Synthetic demo data is only allowed in development / testing / staging (FLASK_ENV={config_name!r})."
        )

    password = os.environ.get("SEED_DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
    try:
        summary = seed_dev_data(password)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    created = sum(summary["created"].values())
    updated = sum(summary["updated"].values())
    click.echo(f"Seed complete (today={summary['today']}): {created} created, {updated} refreshed.")
    for table, count in sorted(summary["created"].items()):
        click.echo(f"  + {table}: {count}")
    click.echo(f"Patient code: {summary['patient_code']}")
    for email, role in summary["accounts"].items():
        click.echo(f"Account: {email} ({role})")
    if "SEED_DEMO_PASSWORD" not in os.environ:
        click.echo(f"Password: {DEFAULT_DEMO_PASSWORD} (default; override with SEED_DEMO_PASSWORD)")
