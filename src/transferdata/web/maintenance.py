"""Cooperative pause at job boundaries; never interrupt a data transaction."""
from django.conf import settings


def is_paused():
    return (settings.STATE_DIR / "maintenance.flag").exists()


def worker_paused(name):
    acknowledgement = settings.STATE_DIR / ("paused-" + name)
    if is_paused():
        acknowledgement.write_text("paused", encoding="ascii")
        return True
    acknowledgement.unlink(missing_ok=True)
    return False
