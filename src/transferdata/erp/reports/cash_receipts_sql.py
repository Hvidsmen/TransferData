"""Translate the agreed cash-receipts report using configuration metadata only."""
import copy
import re
from django.core.exceptions import ValidationError
from transferdata.erp.engine.validators import identifier, read_query
from .cash_receipts import OBJECTS
from .cash_receipts_choices import Metadata, DDS, BUDGET, KINDS, OPERATIONS, STRUCTURE

CODES = {'00-000012': 'Goods', '00-000014': 'Works', '00-000017': 'OCM'}
SCOPES = {'Статьи ДДС': DDS, 'Статьи оборотов бюджета': BUDGET}
CFO = 'Справочник.Подразделения'


def settings_from_rows(rows, area=0):
    """Exact agreed codes, never fuzzy matching or implicit inclusion of CN articles."""
    selected = {}
    for scope, name in SCOPES.items():
        selected[name] = {}
        for code, category in CODES.items():
            matches = [r for r in rows if r.get('Набор') == scope and r.get('Код') == code
                       and r.get('ОбластьДанных') == str(area)]
            if len(matches) != 1 or not re.fullmatch(r'0x[0-9A-Fa-f]{32}', matches[0]['Ссылка']):
                raise ValidationError(f'Нужна одна статья {scope}: {code}, область {area}. Повторите экспорт списка статей.')
            selected[name][category] = matches[0]['Ссылка'].upper().replace('0X', '0x')
        if len(set(selected[name].values())) != 3:
            raise ValidationError('Разные статьи не могут иметь одинаковые ссылки.')
    kinds = [r for r in rows if r.get('Набор') == 'Вид соответствия аналитик'
             and r.get('Код') == 'СтруктураПредприятия_ЦФО' and r.get('ОбластьДанных') == str(area)]
    if len(kinds) != 1 or not re.fullmatch(r'0x[0-9A-Fa-f]{32}', kinds[0]['Ссылка']):
        raise ValidationError('Не найден однозначный вид соответствия СтруктураПредприятия_ЦФО.')
    return {'articles': selected, 'kind': kinds[0]['Ссылка'], 'area': int(area)}


def corrected(value):
    return f'CASE WHEN YEAR({value}) < 3753 THEN {value} ELSE DATEADD(year, -2000, {value}) END'


def generate(objects, settings):
    m = Metadata(copy.deepcopy(objects))
    # Minimal catalogue structures are explicitly described by the helper, not inferred from table suffixes.
    for name in (DDS, BUDGET, CFO):
        if name in m.objects:
            continue
        target = m.targets.get(name, {})
        if not all(target.get(k) for k in ('table', 'id', 'description')):
            raise ValidationError(f'Нужна структура ссылки {name}.')
        m.objects[name] = {'name': name, 'table': target['table'], 'partitions': target.get('partitions', []),
            'properties': [{'name': field, 'columns': [{'name': target[key], 'purpose': 'Value'}]}
                           for field, key in [('Ссылка', 'id'), ('Наименование', 'description')]]}
    c = m.column
    budget, analytics, versions = OBJECTS[2:5]
    area = settings['area']
    if type(area) is not int or not 0 <= area <= 2147483647:
        raise ValidationError('Некорректная область данных.')

    def join_ref(owner, field, owner_alias, alias):
        refs = m.prop(owner, field).get('references', [])
        if len(refs) != 1 or refs[0] not in m.objects:
            raise ValidationError(f'Неоднозначная ссылка {owner}.{field}.')
        target = refs[0]
        return target, f'LEFT JOIN {m.table(target)} AS {identifier(alias)} ON {c(owner, field, owner_alias)} = {c(target, "Ссылка", alias)} AND {m.same_area(owner, owner_alias, target, alias)}'

    def budget_turnovers():
        props = m.objects[budget]['properties']
        if any(type(p.get('is_measure')) is not bool or type(p.get('is_dimension')) is not bool for p in props):
            raise ValidationError('Обновите помощник 1С: нужны роли измерений и ресурсов регистра бюджета.')
        if any(p['name'] == 'ВидДвижения' for p in props):
            raise ValidationError('Этот перевод рассчитан на оборотный регистр бюджета, а не регистр остатков.')
        dims = [p for p in props if p['is_dimension'] or p['name'] in ('Регистратор', 'Период')]
        measures = [p for p in props if p['is_measure']]
        if not measures or 'СуммаУпр' not in {p['name'] for p in measures}:
            raise ValidationError('Не определены ресурсы регистра бюджета.')
        # Keep original physical names so the same typed join builder can consume the virtual table.
        groups = list(dict.fromkeys('[r].' + identifier(col['name']) for p in dims for col in p['columns']))
        groups = list(dict.fromkeys(groups + [m.area(budget, 'r')]))
        sums = []
        nonzero = []
        for p in measures:
            if len(p['columns']) != 1:
                raise ValidationError('Составной ресурс бюджета не поддерживается.')
            col = identifier(p['columns'][0]['name'])
            sums.append(f'SUM([r].{col}) AS {col}')
            nonzero.append(f'SUM([r].{col}) <> 0')
        group_select = list(groups)
        if not m.objects[budget].get('partitions'):
            group_select.remove(m.area(budget, 'r'))
        articles = ', '.join(settings['articles'][BUDGET].values())
        return f'''[BudgetTurnovers] AS (
 SELECT {', '.join(group_select + sums)}
 FROM {m.table(budget)} AS [r] CROSS JOIN [Parameters] AS [p]
 WHERE {c(budget, 'Активность', 'r')} = 0x01 AND {m.area(budget, 'r')} = {area}
   AND {corrected(c(budget, 'Период', 'r'))} <= [p].[AsOf]
   AND {c(budget, 'СтатьяОборотов', 'r')} IN ({articles})
 GROUP BY {', '.join(groups)}
 HAVING {' OR '.join(nonzero)}
)'''

    def branch(reg, doc, cash=False, refund=False):
        joins = []
        source = '[BudgetTurnovers]' if refund else m.table(reg)
        joins.append(f'LEFT JOIN {m.table(doc)} AS [d] ON {c(reg, "Регистратор", "r", "Identity")} = {c(doc, "Ссылка", "d")} AND {m.same_area(reg, "r", doc, "d")}')
        org_owner, org_alias = (doc, 'd') if refund else (reg, 'r')
        org, j = join_ref(org_owner, 'Организация', org_alias, 'org'); joins.append(j)
        bank, j = join_ref(org_owner, 'Касса' if cash else 'БанковскийСчет', org_alias, 'bank'); joins.append(j)
        customer, j = join_ref(doc, 'Контрагент', 'd', 'customer'); joins.append(j)
        partner, j = join_ref(customer, 'Партнер', 'customer', 'partner'); joins.append(j)
        subdivision, j = join_ref(doc, 'Подразделение', 'd', 'subdivision'); joins.append(j)
        article_name = BUDGET if refund else DDS
        article_field = 'СтатьяОборотов' if refund else 'СтатьяДвиженияДенежныхСредств'
        article = c(reg, article_field, 'r')
        joins.append(f'LEFT JOIN {m.table(article_name)} AS [article] ON {article} = {c(article_name, "Ссылка", "article")} AND {m.same_area(reg, "r", article_name, "article")}')
        if refund:
            cfoid = c(reg, 'ЦФО', 'r'); cfotype = m.type_code(CFO); cfotag = '0x08'
            cfobool, cfodate = 'CAST(NULL AS binary(1))', 'CAST(NULL AS datetime)'
        else:
            conditions = [c(doc, 'Подразделение', 'd') + ' = ' + c(analytics, 'ЛеваяАналитика_1', 'a', 'Identity'),
                c(analytics, 'ЛеваяАналитика_1', 'a', 'Tag') + ' = 0x08',
                c(analytics, 'ЛеваяАналитика_1', 'a', 'TypeCode') + ' = ' + m.type_code(STRUCTURE),
                c(analytics, 'ВидСоответствия', 'a') + ' = ' + settings['kind'], m.same_area(reg, 'r', analytics, 'a')]
            for field in ('ЛеваяАналитика_2', 'ЛеваяАналитика_3', 'ПраваяАналитика_2', 'ПраваяАналитика_3'):
                conditions.append(c(analytics, field, 'a', 'Tag') + ' = 0x01')
            joins.append(f'LEFT JOIN {m.table(analytics)} AS [a] ON ' + ' AND '.join(conditions))
            cfoid, cfotype, cfotag, cfobool = [c(analytics, 'ПраваяАналитика_1', 'a', purpose) for purpose in ('Identity', 'TypeCode', 'Tag', 'Boolean')]
            cfodate = corrected(c(analytics, 'ПраваяАналитика_1', 'a', 'DateTime'))
        # Preserve all components of the variant even if an unexpected CFO type has no display text.
        joins.append(f'LEFT JOIN {m.table(CFO)} AS [cfo] ON {cfotag} = 0x08 AND {cfotype} = {m.type_code(CFO)} AND {cfoid} = {c(CFO, "Ссылка", "cfo")} AND {m.same_area(reg, "r", CFO, "cfo")}')
        article_settings = settings['articles'][article_name]
        code = f"CASE {article} WHEN {article_settings['Goods']} THEN N'Goods' WHEN {article_settings['Works']} THEN N'Works' ELSE N'OCM' END"
        purpose = f"CASE {article} WHEN {article_settings['Goods']} THEN N'За Товары' WHEN {article_settings['Works']} THEN N'За Услуги' ELSE N'Контракты прочие' END"
        values = {
            'SourceBranch': str(2 if refund else (1 if cash else 0)),
            'DataArea': m.area(reg, 'r'), 'РегистраторСсылка': c(reg, 'Регистратор', 'r', 'Identity'),
            'РегистраторТип': c(reg, 'Регистратор', 'r', 'TypeCode'),
            'OfficeKey': c(org, 'Префикс', 'org'), 'CFOСсылка': cfoid, 'CFOТип': cfotype,
            'CFOТег': cfotag, 'CFOБулево': cfobool, 'CFOДата': cfodate, 'CFO': c(CFO, 'Наименование', 'cfo'),
            'AmountUSD': c(reg, 'СуммаУпр', 'r'), 'BankAccountKeyName': c(bank, 'Наименование', 'bank'),
            # Refunds originally group by the account reference, not its name. Keep this key
            # to avoid merging different refund accounts with identical descriptions.
            'BankAccountСсылка': c(org_owner, 'Касса' if cash else 'БанковскийСчет', org_alias) if refund else 'CAST(NULL AS binary(16))',
            'FormalCustKeyName': c(customer, 'Наименование', 'customer'), 'RealCustKeyName': c(partner, 'Наименование', 'partner'),
            'Commentary': f'CAST({c(doc, "Комментарий" if cash else "НазначениеПлатежа", "d")} AS nvarchar(254))',
            'TransDate': corrected(c(reg, 'Период', 'r')), 'СтатьяОборотовСсылка': article,
            'СтатьяОборотовТип': m.type_code(article_name), 'СтатьяОборотов': c(article_name, 'Наименование', 'article'),
            'PurposeCode': code, 'PurposeName': purpose,
            'SubdivisionСсылка': c(doc, 'Подразделение', 'd'), 'Subdivision': c(subdivision, 'Наименование', 'subdivision'),
            'DocumentIncomeMoneyNumber': c(doc, 'Номер', 'd'), 'DocumentIncomeMoneyDate': corrected(c(doc, 'Дата', 'd')),
        }
        select = ',\n  '.join(value + ' AS ' + identifier(key) for key, value in values.items())
        where = [c(reg, 'Регистратор', 'r', 'TypeCode') + ' = ' + m.type_code(doc),
            m.area(reg, 'r') + ' = ' + str(area)]
        if refund:
            where.append(c(doc, 'ХозяйственнаяОперация', 'd') + ' = ' + m.enum(OPERATIONS, 'ВозвратОплатыКлиенту'))
        else:
            where += [article + ' IN (' + ', '.join(article_settings.values()) + ')',
                      corrected(c(reg, 'Период', 'r')) + ' <= [p].[AsOf]']
        return f'SELECT {select}\n FROM {source} AS [r] CROSS JOIN [Parameters] AS [p]\n ' + '\n '.join(joins) + '\n WHERE ' + ' AND '.join(where), list(values)

    branches = [branch(OBJECTS[0], OBJECTS[5]), branch(OBJECTS[1], OBJECTS[6], cash=True),
                branch(budget, OBJECTS[7], refund=True), branch(budget, OBJECTS[8], cash=True, refund=True)]
    fields = [name for name in branches[0][1] if name != 'AmountUSD']
    dims = ', '.join(identifier(name) for name in fields)
    vt = '[VT] AS (SELECT ' + dims + ', SUM([AmountUSD]) AS [AmountUSD] FROM [RawRows] GROUP BY ' + dims + ')'
    vtype, vid = c(versions, 'Объект', 'v', 'TypeCode'), c(versions, 'Объект', 'v', 'Identity')
    varea = m.area(versions, 'v')
    # Validate separation layout before using unified aliases in the final CTEs.
    for root in OBJECTS[:3] + [versions]:
        m.same_area(OBJECTS[0], 'r', root, 'v')
    vdates = f'''[VersionDates] AS (
 SELECT {vtype} AS [RefType], {vid} AS [RefId], {varea} AS [DataArea],
 MIN({corrected(c(versions, 'ДатаВерсии', 'v'))}) AS [DateCreate]
 FROM {m.table(versions)} AS [v]
 WHERE {c(versions, 'Объект', 'v', 'Tag')} = 0x08 AND {varea} = {area}
 AND EXISTS (SELECT 1 FROM [VT] AS [t] WHERE [t].[РегистраторТип] = {vtype} AND [t].[РегистраторСсылка] = {vid} AND [t].[DataArea] = {varea})
 GROUP BY {vtype}, {vid}, {varea}
)'''
    output = ['[t].' + identifier(name) for name in fields if name != 'SourceBranch'] + ['[t].[AmountUSD]',
        'CONVERT(int, CONVERT(char(8), [p].[AsOf], 112)) AS [ReportDate]',
        'ISNULL([v].[DateCreate], [t].[TransDate]) AS [DateCreate]',
        "CONCAT([t].[DocumentIncomeMoneyNumber], N' от ', CONVERT(nvarchar(19), [t].[DocumentIncomeMoneyDate], 120)) AS [DocumentIncomeMoneyName]"]
    sql = '''WITH [Parameters] AS (SELECT CONVERT(datetime, SYSUTCDATETIME() AT TIME ZONE 'UTC' AT TIME ZONE 'Russian Standard Time') AS [AsOf]),
''' + budget_turnovers() + ',\n[RawRows] AS (\n' + '\nUNION ALL\n'.join(b[0] for b in branches) + '\n),\n' + vt + ',\n' + vdates + '\nSELECT ' + ',\n '.join(output) + '''
FROM [VT] AS [t] CROSS JOIN [Parameters] AS [p]
INNER JOIN [VersionDates] AS [v] ON [t].[РегистраторТип] = [v].[RefType] AND [t].[РегистраторСсылка] = [v].[RefId] AND [t].[DataArea] = [v].[DataArea]'''
    return read_query(sql)
