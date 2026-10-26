"""Export contact metadata without reading addresses, phone numbers or employee records."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from transferdata.erp.control.management.commands.prepare_payment_schedule_report import Command as PaymentCommand
from transferdata.erp.control.onec import read_metadata, connection_fingerprint
from transferdata.erp.control.onec_contacts import discover_contacts
from transferdata.erp.engine.exceptions import ETLError
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help = 'Find contact storage for a 1C object; export metadata only.'

    def add_arguments(self, parser):
        parser.add_argument('--connection')
        parser.add_argument('--object', default='Справочник.ФизическиеЛица')
        parser.add_argument('--output', default='.state/onec-contacts.json')

    def handle(self, *args, **options):
        connection = PaymentCommand.pick(options['connection'], source=True)
        fingerprint = connection_fingerprint(connection)
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Файл уже существует. Укажите новое имя через --output.')
        try:
            root = read_metadata(connection, options['object'], include_table_parts=True)
            if root.get('name') != options['object']:
                raise CommandError('Помощник вернул структуру другого объекта.')
            result = discover_contacts(connection, root)
            connection.refresh_from_db()
            if fingerprint != connection_fingerprint(connection):
                raise CommandError('Подключение изменилось. Повторите поиск.')
            payload = {'format': 'etl-dev.onec-contact-metadata', 'version': 1, 'object': root['name'],
                       'configuration': root.get('configuration'), 'configuration_version': root.get('version'), **result}
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            for source in result['sources']:
                self.stdout.write(source['name'] + ' → ' + source['table'] + ' · ' + source['relationship'])
            for warning in result['warnings']:
                self.stdout.write(self.style.WARNING(warning))
            self.stdout.write(f'Файл метаданных: {output}. Персональные значения не выгружались.')
        except (ETLError, ValidationError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)) from None
