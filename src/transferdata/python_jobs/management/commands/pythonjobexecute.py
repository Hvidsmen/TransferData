import json
from django.core.management.base import BaseCommand, CommandError
from transferdata.python_jobs.models import Run
from transferdata.python_jobs.handlers import commercial_headers
from transferdata.python_jobs.services import PREFIX


class Command(BaseCommand):
    help = 'Internal isolated execution of a claimed Python job.'

    def add_arguments(self, parser):
        parser.add_argument('--run', type=int, required=True)

    def handle(self, *args, **options):
        run = Run.objects.get(pk=options['run'], status='running')
        if run.snapshot['handler'] != 'commercial_headers':
            raise CommandError('Неизвестный встроенный обработчик')
        def progress(**values):
            self.stdout.write(PREFIX + json.dumps(values, ensure_ascii=False))
            self.stdout.flush()
        try:
            rows = commercial_headers(run.snapshot, progress)
        except Exception as exc:
            # ODBC errors can include SQL values and secrets: log only the exception class.
            raise CommandError(f'Обработчик заголовков: {type(exc).__name__}. Последний документ отменён. Проверьте структуру таблиц, длину Header, подключение и права записи.') from None
        self.stdout.write(PREFIX + json.dumps({'result': True, 'rows': rows}))
