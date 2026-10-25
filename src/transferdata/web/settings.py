import os
from pathlib import Path
import secrets

STATE_DIR = Path(os.environ.get("TRANSFERDATA_STATE_DIR", ".state")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)
key_file = STATE_DIR / "django-secret.key"
if not key_file.exists():
    try:
        with key_file.open("x", encoding="ascii") as handle:
            handle.write(secrets.token_urlsafe(64))
        key_file.chmod(0o600)
    except FileExistsError:
        pass
SECRET_KEY = os.environ.get("TRANSFERDATA_SECRET_KEY") or key_file.read_text(encoding="ascii")
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    "TRANSFERDATA_ALLOWED_HOSTS", "vm-dwh,localhost,127.0.0.1,[::1],192.168.221.178,etl-dev-portal.daichi.ru"
).split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([
    "http://etl-dev-portal.daichi.ru", "https://etl-dev-portal.daichi.ru",
    "http://etl-dev-portal.daichi.ru:8003", "https://etl-dev-portal.daichi.ru:8003",
    *[origin.strip() for origin in os.environ.get("TRANSFERDATA_CSRF_ORIGINS", "").split(",") if origin.strip()],
]))
ROOT_URLCONF = "transferdata.web.urls"
WSGI_APPLICATION = "transferdata.web.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.humanize", "transferdata.web.panel",
    "transferdata.erp.control.apps.ERPConfig", "transferdata.python_jobs.apps.PythonJobsConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "transferdata.web.portal_update.middleware.PauseWrites",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
              "DIRS": [Path(__file__).parent / "panel" / "templates"],
              "APP_DIRS": True, "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request",
                  "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages", "transferdata.web.panel.context_processors.operations",
                  "transferdata.web.panel.context.portal",
              ]}}]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": STATE_DIR / "panel.sqlite3", "OPTIONS": {"timeout": 30}}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = STATE_DIR / "static"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("TRANSFERDATA_HTTPS", "0") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
