from django.core.management.base import BaseCommand
from django.conf import settings
from waitress import serve
from transferdata.web.wsgi import application


class Command(BaseCommand):
    help = "Serve the Django panel with Waitress on Windows or Linux."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=8003)

    def handle(self, *args, **options):
        self.stdout.write(f"{settings.PORTAL_NAME} listening on {options['host']}:{options['port']}")
        serve(application, host=options["host"], port=options["port"], threads=4)
