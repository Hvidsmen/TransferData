"""Read-only catalogue/usage queries for choosing the cash-receipts report settings."""
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier
from . import cash_receipts

DDS = 'Справочник.СтатьиДвиженияДенежныхСредств'
BUDGET = 'Справочник.бит_СтатьиОборотов'
KINDS = 'Справочник.бит_ВидыСоответствийАналитик'
OPERATIONS = 'Перечисление.ХозяйственныеОперации'
STRUCTURE = 'Справочник.СтруктураПредприятия'
# One helper invocation reads the configuration once for all required objects.
OBJECTS = cash_receipts.OBJECTS[:5] + cash_receipts.OBJECTS[7:9] + [DDS, BUDGET, KINDS, OPERATIONS]
ZERO = '0x' + '00' * 16


@dataclass
class ChoiceQuery:
    scope: str
    sql: str
    parameters: tuple


class Metadata:
    def __init__(self, objects):
        self.objects = objects
        self.targets = {}
        for obj in objects.values():
            for target in obj.get('reference_targets', []):
                name = target['name']
                if name in self.targets and self.targets[name].get('type_code') != target.get('type_code'):
                    raise ValidationError('Метаданные содержат противоречивые коды типов ссылок.')
                self.targets[name] = target

    def table(self, name):
        return '[dbo].' + identifier(self.objects[name]['table'])

    def prop(self, name, field):
        matches = [p for p in self.objects[name]['properties'] if p['name'] == field]
        if len(matches) != 1:
            raise ValidationError(f'Не найден однозначный реквизит {name}.{field}.')
        return matches[0]

    def column(self, name, field, alias, purpose='Value'):
        parts = [c for c in self.prop(name, field)['columns'] if c.get('purpose') == purpose]
        if len(parts) != 1:
            raise ValidationError(f'Не найден компонент {purpose}: {name}.{field}.')
        return identifier(alias) + '.' + identifier(parts[0]['name'])

    def area(self, name, alias):
        # This report expects the single separation dimension found in the supplied configuration.
        parts = self.objects[name].get('partitions', [])
        if len(parts) > 1:
            raise ValidationError('Для нескольких разделителей данных требуется отдельная настройка отчёта.')
        return identifier(alias) + '.' + identifier(parts[0]['source']) if parts else 'CAST(0 AS int)'

    def same_area(self, left, la, right, ra):
        a = self.objects[left].get('partitions', [])
        b = self.objects[right].get('partitions', [])
        if [p['key'] for p in a] != [p['key'] for p in b]:
            raise ValidationError('Разделители данных объектов не совпадают; соединение отменено.')
        return self.area(left, la) + ' = ' + self.area(right, ra)

    def type_code(self, name):
        value = self.targets.get(name, {}).get('type_code')
        if type(value) is not int or not 0 < value <= 2147483647:
            raise ValidationError(f'Не найден код типа: {name}.')
        return f'0x{value:08X}'

    def enum(self, name, value):
        import re
        values = [item['hex'] for item in self.targets.get(name, {}).get('values', []) if item['name'] == value]
        if len(values) != 1 or not re.fullmatch('[0-9A-Fa-f]{32}', values[0]):
            raise ValidationError(f'Не найдено значение перечисления {name}.{value}.')
        return '0x' + values[0]


def queries(objects, as_of: datetime, counts=True):
    m = Metadata(objects)
    if not 1753 <= as_of.year <= 7999:
        raise ValidationError('Дата должна быть в диапазоне 1753–7999 годов.')
    shifted = as_of.replace(year=as_of.year + 2000)
    incoming1, incoming2, budget, analytics = cash_receipts.OBJECTS[:4]
    receipt1, receipt2, refund1, refund2 = cash_receipts.OBJECTS[5:9]

    def period(name, alias):
        value = m.column(name, 'Период', alias)
        return f"(({value} < CONVERT(datetime, '37530101', 112) AND {value} <= ?) OR ({value} >= CONVERT(datetime, '37530101', 112) AND {value} <= ?))"

    def income(name, document):
        m.same_area(name, 'r', DDS, 'c')
        return f"SELECT {m.column(name, 'СтатьяДвиженияДенежныхСредств', 'r')} AS [ArticleId], {m.area(name, 'r')} AS [DataArea] FROM {m.table(name)} AS [r] WHERE {m.column(name, 'Регистратор', 'r', 'TypeCode')} = {m.type_code(document)} AND {period(name, 'r')}"

    def refund(document):
        m.same_area(budget, 'r', BUDGET, 'c')
        return f"SELECT {m.column(budget, 'СтатьяОборотов', 'r')} AS [ArticleId], {m.area(budget, 'r')} AS [DataArea] FROM {m.table(budget)} AS [r] INNER JOIN {m.table(document)} AS [d] ON {m.column(budget, 'Регистратор', 'r', 'Identity')} = {m.column(document, 'Ссылка', 'd')} AND {m.column(budget, 'Регистратор', 'r', 'TypeCode')} = {m.type_code(document)} AND {m.same_area(budget, 'r', document, 'd')} WHERE {m.column(budget, 'Активность', 'r')} = 0x01 AND {m.column(document, 'ХозяйственнаяОперация', 'd')} = {m.enum(OPERATIONS, 'ВозвратОплатыКлиенту')} AND {period(budget, 'r')}"

    def kinds():
        m.same_area(analytics, 'r', KINDS, 'c')
        conditions = [m.column(analytics, 'ЛеваяАналитика_1', 'r', 'Tag') + ' = 0x08',
                      m.column(analytics, 'ЛеваяАналитика_1', 'r', 'TypeCode') + ' = ' + m.type_code(STRUCTURE)]
        for name in ['ЛеваяАналитика_2', 'ЛеваяАналитика_3', 'ПраваяАналитика_2', 'ПраваяАналитика_3']:
            conditions.append(m.column(analytics, name, 'r', 'Tag') + ' = 0x01')
        return f"SELECT {m.column(analytics, 'ВидСоответствия', 'r')} AS [ArticleId], {m.area(analytics, 'r')} AS [DataArea] FROM {m.table(analytics)} AS [r] WHERE " + ' AND '.join(conditions)

    def catalogue(scope, name, movement_select, parameters):
        key, area = m.column(name, 'Ссылка', 'c'), m.area(name, 'c')
        desc = m.column(name, 'Наименование', 'c')
        code = m.column(name, 'Код', 'c') if any(p['name'] == 'Код' for p in m.objects[name]['properties']) else 'CAST(NULL AS nvarchar(100))'
        if not counts:
            sql = f'SELECT {key} AS [Reference], {code} AS [Code], {desc} AS [Name], {area} AS [DataArea], CAST(NULL AS bigint) AS [UsageCount] FROM {m.table(name)} AS [c] WHERE {key} <> {ZERO} ORDER BY {area}, {desc}, {key}'
            return ChoiceQuery(scope, sql, ())
        sql = f'''WITH [movements] AS ({movement_select}), [usage] AS (
    SELECT [ArticleId], [DataArea], COUNT_BIG(*) AS [UsageCount]
    FROM [movements] WHERE [ArticleId] <> {ZERO} GROUP BY [ArticleId], [DataArea]
)
SELECT COALESCE({key}, [u].[ArticleId]) AS [Reference], {code} AS [Code],
    COALESCE({desc}, N'Ссылка без записи в справочнике') AS [Name],
    COALESCE({area}, [u].[DataArea]) AS [DataArea], COALESCE([u].[UsageCount], 0) AS [UsageCount]
FROM {m.table(name)} AS [c] FULL OUTER JOIN [usage] AS [u]
    ON {key} = [u].[ArticleId] AND {area} = [u].[DataArea]
WHERE COALESCE({key}, [u].[ArticleId]) <> {ZERO}
ORDER BY [DataArea], [Name], [Reference]'''
        return ChoiceQuery(scope, sql, parameters)

    # Lazy construction lets --no-counts avoid scanning registers and reading operation filters.
    p = (as_of, shifted, as_of, shifted)
    return [catalogue('Статьи ДДС', DDS, (income(incoming1, receipt1) + '\nUNION ALL\n' + income(incoming2, receipt2)) if counts else '', p),
            catalogue('Статьи оборотов бюджета', BUDGET, (refund(refund1) + '\nUNION ALL\n' + refund(refund2)) if counts else '', p),
            catalogue('Вид соответствия аналитик', KINDS, kinds() if counts else '', ())]
