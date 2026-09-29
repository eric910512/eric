import copy

from flask import current_app

from app.core.api import ok
from app.modules.settings import settings_bp


@settings_bp.get("/public")
def public_settings():
    """Institution settings shown before and after sign-in (api-design.md §12): organization,
    contacts, disclaimers. Public — the login page shows the organization name.

    Phase 1–2 source: the backend config file (``Config.INSTITUTION``); Phase 3 reads
    ``institution_settings`` (``is_public=true``) and ``meta.source`` becomes ``database``.
    """
    return ok(copy.deepcopy(current_app.config["INSTITUTION"]), meta={"source": "config_file"})
