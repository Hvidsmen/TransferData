from django.core.management.base import BaseCommand
from transferdata.erp.control.services import enqueue_due_jobs


class Command(BaseCommand):
    help = 'Enqueue due daily, weekly or monthly jobs once per matching day.'

    def handle(self, *args, **options):
        self.stdout.write(f'Добавлено: {enqueue_due_jobs()}')
