"""Daily payment-schedule report; physical names come exclusively from 1C metadata."""
import re
from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier, read_query
from .cash_receipts_choices import Metadata
from .cash_receipts_sql import corrected

REGISTER = 'РегистрНакопления.даичи_ГрафикиПлатежей'
REGISTRY = 'Документ.бит_кзн_РеестрПлатежей'
POSITION = 'Документ.бит_кзн_ПлатежнаяПозиция'
PART = REGISTRY + '.ТаблицаПлатежи'
ZERO = '0x' + '00' * 16


class ReportMetadata(Metadata):
    def same_area(self, left, la, right, ra):
        # A descriptor without separation columns denotes globally shared data.
        if not self.objects[right].get('partitions'):
            return '1 = 1'
        return super().same_area(left, la, right, ra)


def literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def concrete_references(obj, field):
    properties = [p for p in obj['properties'] if p['name'] == field]
    refs = properties[0].get('references', []) if len(properties) == 1 else []
    if not refs or any(len(r.split('.')) != 2 or not r.startswith(('Документ.', 'Справочник.', 'Перечисление.')) for r in refs):
        raise ValidationError(f'Нужен конкретный тип ссылки {obj["name"]}.{field}.')
    return refs


def with_table_part(objects):
    objects = dict(objects)
    parts = [p for p in objects[REGISTRY].get('table_parts', []) if p['name'] == 'ТаблицаПлатежи']
    if len(parts) != 1 or not parts[0].get('properties'):
        raise ValidationError('Нужна структура табличной части РеестрПлатежей.ТаблицаПлатежи. Обновите помощник 1С.')
    objects[PART] = dict(parts[0], name=PART)
    return objects


def generate(objects, *, usd=True, usd_resource=None):
    """No date restriction: includes all history and future schedule records."""
    objects = with_table_part(objects)
    m = ReportMetadata(objects)
    c = m.column
    reg = objects[REGISTER]
    dims, measures = [], []
    for p in reg['properties']:
        if type(p.get('is_dimension')) is not bool or type(p.get('is_measure')) is not bool:
            raise ValidationError('Обновите помощник: нужны роли измерений и ресурсов регистра.')
        if p['is_dimension']:
            dims.extend('[r].' + identifier(col['name']) for col in p['columns'])
        if p['is_measure']:
            if len(p['columns']) != 1:
                raise ValidationError('Составной ресурс регистра не поддерживается.')
            measures.append(p)
    if not measures or 'СуммаПлатежа' not in {p['name'] for p in measures}:
        raise ValidationError('Ресурс СуммаПлатежа не найден.')
    for name in ('ЗаказПоставщику', 'ТипПлатежа', 'ЗаявкаНаОплату', 'ЕстьОплата', 'Инвойс', 'ДатаКурса'):
        if not m.prop(REGISTER, name)['is_dimension']:
            raise ValidationError(f'{name} не является измерением виртуальной таблицы Обороты.')
    if usd:
        resource_names = {p['name'] for p in measures}
        if usd_resource:
            if usd_resource not in resource_names or usd_resource == 'СуммаПлатежа':
                raise ValidationError('Укажите отдельный ресурс готовой суммы управленческого учёта из регистра.')
        else:
            candidates = resource_names.intersection({'СуммаУпр', 'СуммаПлатежаУпр'})
            if len(candidates) != 1:
                raise ValidationError('Не найден однозначный ресурс готовой суммы управленческого учёта. Ресурсы регистра: '
                    + ', '.join(sorted(resource_names)) + '. Укажите --usd-resource с точным именем готового ресурса. Курсы не используются.')
            usd_resource = candidates.pop()
    day = 'CONVERT(datetime, CONVERT(date, ' + corrected(c(REGISTER, 'Период', 'r')) + '))'
    group = list(dict.fromkeys([day, *dims] + ([m.area(REGISTER, 'r')] if reg.get('partitions') else [])))
    physical = [expression for expression in group if expression != day]
    # Registers of balances carry RecordKind (0=receipt, 1=expense); turnover registers do not.
    kind = next((p for p in reg['properties'] if p['name'] == 'ВидДвижения'), None)
    def sum_resource(p):
        value = '[r].' + identifier(p['columns'][0]['name'])
        if kind:
            value = f'CASE WHEN {c(REGISTER, "ВидДвижения", "r")} = 0 THEN {value} ELSE -{value} END'
        return 'SUM(' + value + ')'
    sums = [sum_resource(p) + ' AS ' + identifier(p['columns'][0]['name']) for p in measures]
    turnover = f'''[DailyTurnovers] AS (
 SELECT {day} AS [PaymentDay], {', '.join(physical + sums)}
 FROM {m.table(REGISTER)} AS [r]
 WHERE {c(REGISTER, 'Активность', 'r')} = 0x01
 GROUP BY {', '.join(group)}
 HAVING {' OR '.join(sum_resource(p) + ' <> 0' for p in measures)}
)'''
    fields, joins = [], []
    def field(expression, name):
        identifier(name)
        fields.append(expression + ' AS ' + identifier(name))
    def ref(owner, prop, alias):
        refs = concrete_references(objects[owner], prop)
        parts = m.prop(owner, prop)['columns']
        if len(parts) == 1:
            if len(refs) != 1:
                raise ValidationError(f'Неоднозначный одиночный тип {owner}.{prop}.')
            return c(owner, prop, alias), m.type_code(refs[0]), '0x08', refs
        return c(owner, prop, alias, 'Identity'), c(owner, prop, alias, 'TypeCode'), c(owner, prop, alias, 'Tag'), refs
    def display(owner, prop, alias, output):
        raw, code, tag, refs = ref(owner, prop, alias)
        field(raw, output + 'Ссылка'); field(code, output + 'Тип')
        cases, dates, numbers, targets = [], [], [], {}
        for name in refs:
            target = m.targets.get(name, {})
            actual = m.type_code(name)
            predicate = f'{tag} = 0x08 AND {code} = {actual}'
            if name.startswith('Перечисление.'):
                values = []
                for item in target.get('values', []):
                    if not re.fullmatch('[A-Fa-f0-9]{32}', item.get('hex', '')):
                        raise ValidationError('Некорректная ссылка перечисления.')
                    values.append(f"WHEN 0x{item['hex']} THEN {literal(item['name'])}")
                if not values:
                    raise ValidationError(f'Нет значений перечисления {name}.')
                cases.append(f"WHEN {predicate} THEN CASE {raw} {' '.join(values)} END")
                continue
            if not target.get('table') or not target.get('id'):
                raise ValidationError(f'Нет структуры ссылки {name}.')
            join_alias = 'j' + str(len(joins))
            area_object = dict(target, properties=[])
            # Reference descriptors have their own separation columns, validated against the owner.
            descriptor_name = '__display_' + join_alias
            m.objects[descriptor_name] = area_object
            joins.append(f'LEFT JOIN [dbo].{identifier(target["table"])} AS {identifier(join_alias)} ON {predicate} AND {raw} = {identifier(join_alias)}.{identifier(target["id"])} AND {m.same_area(owner, alias, descriptor_name, join_alias)}')
            targets[name] = join_alias
            if name.startswith('Документ.'):
                if not target.get('date') or not target.get('number'):
                    raise ValidationError(f'Нет даты/номера документа {name}.')
                at = corrected(identifier(join_alias) + '.' + identifier(target['date']))
                number = identifier(join_alias) + '.' + identifier(target['number'])
                cases.append(f"WHEN {predicate} THEN CONCAT({literal(name.split('.', 1)[1] + ' №')}, {number}, N' от ', CONVERT(nvarchar(19), {at}, 120))")
                dates.append(f'WHEN {predicate} THEN {at}')
                numbers.append(f'WHEN {predicate} THEN {number}')
            else:
                if not target.get('description'):
                    raise ValidationError(f'Нет наименования ссылки {name}.')
                cases.append(f'WHEN {predicate} THEN {identifier(join_alias)}.{identifier(target["description"])}')
        field('CASE ' + ' '.join(cases) + ' END', output)
        if dates:
            field('CASE ' + ' '.join(dates) + ' END', output + 'Дата')
            field('CASE ' + ' '.join(numbers) + ' END', output + 'Номер')
        return raw, code, tag, targets

    field('[t].[PaymentDay]', 'Период'); field('[t].[PaymentDay]', 'ДатаПлатежа')
    _, _, _, orders = display(REGISTER, 'ЗаказПоставщику', 't', 'ЗаказПоставщику')
    if len(orders) != 1:
        raise ValidationError('Для заказа поставщику нужен один тип документа.')
    order, order_alias = next(iter(orders.items()))
    if order not in objects:
        raise ValidationError('Нужна полная структура заказа поставщику.')
    display(REGISTER, 'ТипПлатежа', 't', 'ТипПлатежа')
    request_id, request_type, request_tag, _ = display(REGISTER, 'ЗаявкаНаОплату', 't', 'ЗаявкаНаОплату')
    if concrete_references(objects[REGISTER], 'ЗаявкаНаОплату') != [POSITION]:
        raise ValidationError('Для заявки ожидается тип Документ.бит_кзн_ПлатежнаяПозиция из исходного запроса.')
    display(order, 'Организация', order_alias, 'Организация')
    display(order, 'Контрагент', order_alias, 'Поставщик')
    display(order, 'Валюта', order_alias, 'ВалютаПлатежа')
    _, invoice_type, invoice_tag, invoices = display(REGISTER, 'Инвойс', 't', 'Инвойс')
    invoice_amounts = []
    for name, alias in invoices.items():
        if name not in objects:
            raise ValidationError('Нужна полная структура инвойса.')
        invoice_amounts.append(f'WHEN {invoice_tag} = 0x08 AND {invoice_type} = {m.type_code(name)} THEN {c(name, "СуммаДокумента", alias)}')
    if not invoice_amounts:
        raise ValidationError('Для инвойса ожидается документ с суммой документа.')
    field('CASE ' + ' '.join(invoice_amounts) + ' END', 'СуммаИнвойса')
    field(c(REGISTER, 'СуммаПлатежа', 't'), 'Сумма')
    if usd:
        field(c(REGISTER, usd_resource, 't'), 'USD')
        field("N'USD'", 'ВалютаUSD')
    field(corrected(c(REGISTER, 'ДатаКурса', 't')), 'ДатаКурса')
    paid = c(REGISTER, 'ЕстьОплата', 't') + ' = 0x01'
    field('CAST(CASE WHEN ' + paid + ' THEN 1 ELSE 0 END AS bit)', 'ЕстьОплата')
    position_id, position_type, position_tag, _ = ref(PART, 'ПлатежнаяПозиция', 'p')
    # EXISTS is equivalent to the DISTINCT request list followed by LEFT JOIN: no duplicated sums.
    registered = f'''EXISTS (SELECT 1 FROM {m.table(PART)} AS [p]
 INNER JOIN {m.table(REGISTRY)} AS [d] ON {c(PART, 'Ссылка', 'p')} = {c(REGISTRY, 'Ссылка', 'd')} AND {m.same_area(PART, 'p', REGISTRY, 'd')}
 WHERE {c(REGISTRY, 'Проведен', 'd')} = 0x01 AND {position_tag} = 0x08 AND {request_tag} = 0x08
 AND {position_id} = {request_id} AND {position_type} = {request_type} AND {m.same_area(PART, 'p', REGISTER, 't')})'''
    field(f"CASE WHEN {paid} THEN N'Факт' WHEN {request_id} <> {ZERO} AND {registered} THEN N'Заявка в реестре' WHEN {request_id} <> {ZERO} THEN N'План (Заявка создана)' ELSE N'План (Заявка не создана)' END", 'СтатусПлатежа')
    # Keep the separation dimension in the export; joins cannot cross areas.
    field(m.area(REGISTER, 't'), 'ОбластьДанных')
    select = 'SELECT\n ' + ',\n '.join(fields) + '\nFROM [DailyTurnovers] AS [t]\n' + '\n'.join(joins)
    return read_query('WITH ' + turnover + '\n' + select)
