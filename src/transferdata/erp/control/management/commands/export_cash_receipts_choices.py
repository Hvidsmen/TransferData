"""Export human-readable choices; does not create or run a transfer job."""
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyodbc
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from transferdata.erp.control.models import DatabaseConnection
from transferdata.erp.control.onec import connection_fingerprint, read_metadata
from transferdata.erp.engine.connections import connect
from transferdata.erp.engine.exceptions import ETLError
from transferdata.erp.reports.cash_receipts_choices import OBJECTS, queries


COLUMNS = ['Набор', 'Код', 'Наименование', 'Движений/соответствий', 'ОбластьДанных',
           'Ссылка', 'ВключитьВОтчет', 'Категория']


def cell(value):
    if value is None:
        return ''
    if isinstance(value, (bytes, bytearray, memoryview)):
        return '0x' + bytes(value).hex().upper()
    value = str(value)
    # CSV is meant for Excel; configuration names must not become spreadsheet formulas.
    if value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        value = "'" + value
    return value


class Command(BaseCommand):
    help = 'Export article names/codes/usage and analytics mapping kinds for cash receipts, using only SELECT.'

    def add_arguments(self, parser):
        parser.add_argument('--connection', help='Source connection name; optional if exactly one source is active.')
        parser.add_argument('--output', default='.state/cash-receipts-choices.csv')
        parser.add_argument('--as-of', help='ISO date/time, interpreted in Europe/Moscow if timezone is omitted.')
        parser.add_argument('--no-counts', action='store_true', help='Fast catalogue-only export without register scans.')
        parser.add_argument('--timeout', type=int, help='SQL timeout in seconds (1–86400); defaults to connection setting.')

    def handle(self, *args, **options):
        candidates = DatabaseConnection.objects.filter(is_active=True).exclude(role='TARGET')
        if options['connection']:
            candidates = candidates.filter(name=options['connection'])
        selected = list(candidates[:2])
        if len(selected) != 1:
            raise CommandError('Выберите источник: добавьте --connection "Название подключения" из портала.')
        connection = selected[0]
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Файл уже существует. Укажите новое имя через --output; существующий файл не перезаписывается.')
        if options['timeout'] is not None and not 1 <= options['timeout'] <= 86400:
            raise CommandError('Таймаут должен быть от 1 до 86400 секунд.')
        zone = ZoneInfo('Europe/Moscow')
        try:
            at = datetime.fromisoformat(options['as_of']) if options['as_of'] else datetime.now(zone)
        except ValueError:
            raise CommandError('Укажите дату в формате 2026-10-23T18:00:00.') from None
        if at.tzinfo is not None:
            at = at.astimezone(zone)
        at = at.replace(tzinfo=None)
        fingerprint = connection_fingerprint(connection)
        self.stdout.write('Читаем метаданные статей и источников отчёта…')
        try:
            payload = read_metadata(connection, names=OBJECTS)
            objects = {obj['name']: obj for obj in payload.get('objects', [])}
            if set(OBJECTS) - set(objects):
                raise CommandError('Не получены все структуры объектов. Проверьте источник и обновите помощник 1С.')
            prepared = queries(objects, at, counts=not options['no_counts'])
            self.stdout.write(f'Состояние на {at:%d.%m.%Y %H:%M:%S} · Москва')
            records = []
            with connect(connection, timeout=options['timeout'] or connection.query_timeout) as database:
                for index, query in enumerate(prepared, 1):
                    self.stdout.write(f'[{index}/{len(prepared)}] {query.scope}' + (' · подсчёт записей…' if not options['no_counts'] else ''))
                    cursor = database.cursor()
                    try:
                        cursor.execute(query.sql, *query.parameters)
                        names = [item[0] for item in cursor.description]
                        for row in cursor.fetchall():
                            data = dict(zip(names, row))
                            records.append([query.scope, data.get('Code'), data.get('Name'), data.get('UsageCount'),
                                            data.get('DataArea'), data.get('Reference'), '', ''])
                    finally:
                        cursor.close()
            connection.refresh_from_db()
            if connection_fingerprint(connection) != fingerprint:
                raise CommandError('Подключение изменилось во время чтения. Повторите экспорт.')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream, delimiter=';')
                writer.writerow(COLUMNS)
                writer.writerows([[cell(value) for value in row] for row in records])
        except (ValidationError, ETLError) as exc:
            raise CommandError(' '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)) from None
        except pyodbc.Error:
            raise CommandError('Не удалось прочитать список статей. Проверьте подключение и права SELECT. Для быстрого списка без подсчёта движений добавьте --no-counts.') from None
        except FileExistsError:
            raise CommandError('Выходной файл уже существует. Укажите другое имя.') from None
        self.stdout.write(self.style.SUCCESS(f'Готово: {len(records)} строк. {output}'))
        self.stdout.write('Выберите статьи по наименованиям: ВключитьВОтчет = 1, Категория = Goods / Works / OCM.')
        self.stdout.write('Для вида соответствия выберите одну строку на область данных; категорию оставьте пустой.')
