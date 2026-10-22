import copy
from unittest.mock import MagicMock, patch
import csv
import json
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError

from transferdata.erp.reports.cash_receipts import OBJECTS
from transferdata.erp.reports.cash_receipts_choices import DDS, BUDGET, KINDS, OPERATIONS, STRUCTURE
from transferdata.erp.reports.cash_receipts_sql import CFO, generate, settings_from_rows


@pytest.fixture
def report_inputs():
    org, bank, cash, customer, partner = ['Справочник.' + n for n in ('Организации', 'Счета', 'Кассы', 'Контрагенты', 'Партнеры')]
    objects = {}
    counter = 0
    def prop(name, refs=(), purposes=('Value',), **flags):
        nonlocal counter
        counter += 1
        return dict(name=name, references=list(refs), columns=[{'name': f'_c{counter}{p}', 'purpose': p} for p in purposes],
                    is_measure=False, is_dimension=False) | flags
    def obj(name, props):
        objects[name] = dict(name=name, table='_Table' + str(len(objects)), properties=props,
                             partitions=[{'key': 'shared', 'source': '_Area'}], configuration='test', version=42)
    for name in [org, bank, cash, customer, partner, STRUCTURE, CFO, DDS, BUDGET, KINDS]:
        fields = [prop('Ссылка'), prop('Наименование'), prop('Код')]
        if name == org: fields += [prop('Префикс')]
        if name == customer: fields += [prop('Партнер', [partner])]
        obj(name, fields)
    for doc in OBJECTS[5:9]:
        obj(doc, [prop('Ссылка'), prop('Дата'), prop('Номер'), prop('ХозяйственнаяОперация', [OPERATIONS]),
                  prop('Организация', [org]), prop('БанковскийСчет', [bank]), prop('Касса', [cash]),
                  prop('Контрагент', [customer]), prop('Подразделение', [STRUCTURE]), prop('Комментарий'), prop('НазначениеПлатежа')])
    for reg in OBJECTS[:3]:
        obj(reg, [prop('Регистратор', OBJECTS[5:9], ('Identity', 'TypeCode')), prop('Период'), prop('Активность'),
                  prop('СуммаУпр', is_measure=True), prop('Сумма', is_measure=True),
                  prop('СтатьяДвиженияДенежныхСредств', [DDS]), prop('СтатьяОборотов', [BUDGET], is_dimension=True),
                  prop('ЦФО', [CFO], is_dimension=True), prop('Организация', [org]), prop('БанковскийСчет', [bank]), prop('Касса', [cash])])
    obj(OBJECTS[3], [prop('ВидСоответствия', [KINDS])] + [prop(name, purposes=('Identity', 'TypeCode', 'Tag', 'Boolean', 'DateTime'))
        for name in ('ЛеваяАналитика_1', 'ЛеваяАналитика_2', 'ЛеваяАналитика_3', 'ПраваяАналитика_1', 'ПраваяАналитика_2', 'ПраваяАналитика_3')])
    obj(OBJECTS[4], [prop('Объект', purposes=('Identity', 'TypeCode', 'Tag')), prop('ДатаВерсии')])
    obj(OPERATIONS, [])
    objects[OPERATIONS]['reference_targets'] = [dict(name=name, type_code=i + 100, table=o['table'],
        id=next(p['columns'][0]['name'] for p in o['properties'] if p['name'] == 'Ссылка'),
        description=next(p['columns'][0]['name'] for p in o['properties'] if p['name'] == 'Наименование') if name.startswith('Справочник.') else None,
        partitions=o['partitions']) for i, (name, o) in enumerate(objects.items()) if name.startswith(('Документ.', 'Справочник.'))]
    objects[OPERATIONS]['reference_targets'].append(dict(name=OPERATIONS, type_code=999,
        values=[dict(name='ВозвратОплатыКлиенту', hex='AB' * 16)]))
    rows = []
    for i, scope in enumerate(('Статьи ДДС', 'Статьи оборотов бюджета')):
        for n, code in enumerate(('00-000012', '00-000014', '00-000017')):
            rows.append({'Набор': scope, 'Код': code, 'ОбластьДанных': '0', 'Ссылка': '0x' + f'{10*i+n+1:032X}'})
    rows.append({'Набор': 'Вид соответствия аналитик', 'Код': 'СтруктураПредприятия_ЦФО', 'ОбластьДанных': '0', 'Ссылка': '0x' + 'CC'*16})
    return objects, rows


def test_exact_codes_include_historical_works_and_exclude_extras(report_inputs):
    _, rows = report_inputs
    rows.append(dict(rows[0], Код='OTHER', Ссылка='0x' + 'FF'*16))
    settings = settings_from_rows(rows)
    assert len(settings['articles'][DDS]) == 3
    assert settings['articles'][DDS]['Works'] == rows[1]['Ссылка']
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValidationError, match='одна статья'):
        settings_from_rows(rows)


def test_preserves_financial_rules_and_reference_components(report_inputs):
    objects, rows = report_inputs
    sql = generate(objects, settings_from_rows(rows))
    assert sql.count('UNION ALL') == 3
    assert 'INNER JOIN [VersionDates]' in sql
    assert 'Russian Standard Time' in sql and 'CONVERT(int, CONVERT(char(8)' in sql
    assert 'HAVING SUM(' in sql and sql.count(' <> 0') == 2
    assert sql.count('= 0x01') == 9  # budget activity plus four undefined conditions in each income branch
    for field in ['CFOТег', 'CFOТип', 'CFOСсылка', 'CFOБулево', 'CFOДата', 'РегистраторТип', 'СтатьяОборотовТип']:
        assert '[' + field + ']' in sql
    for reg in OBJECTS[:2]:
        active = next(p['columns'][0]['name'] for p in objects[reg]['properties'] if p['name'] == 'Активность')
        assert active not in sql
    assert 'TOP 1' not in sql and 'DISTINCT' not in sql
    assert 'MIN(CASE WHEN YEAR(' in sql and 'DATEADD(year, -2000' in sql
    assert '0x' + 'AB'*16 in sql


def test_missing_roles_and_balance_register_are_rejected(report_inputs):
    objects, rows = report_inputs
    budget = objects[OBJECTS[2]]
    budget['properties'][0].pop('is_dimension')
    with pytest.raises(ValidationError, match='роли'):
        generate(objects, settings_from_rows(rows))
    budget['properties'][0]['is_dimension'] = False
    budget['properties'].append(dict(name='ВидДвижения', columns=[], is_measure=False, is_dimension=False))
    with pytest.raises(ValidationError, match='остатков'):
        generate(objects, settings_from_rows(rows))


def test_descriptors_resolve_missing_catalogue_objects(report_inputs):
    objects, rows = report_inputs
    for name in (DDS, BUDGET, CFO):
        del objects[name]
    assert '[СтатьяОборотовСсылка]' in generate(objects, settings_from_rows(rows))


@pytest.mark.django_db
def test_command_checks_source_before_creating_manual_job(job, report_inputs, tmp_path):
    from contextlib import contextmanager
    from transferdata.erp.control.models import ETLJob
    objects, rows = report_inputs
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({'format': 'etl-dev.onec-report-metadata', 'version': 1, 'report': 'cash_receipts',
        'issues': [], 'source': {'connection': job.source_connection.name, 'database': job.source_connection.database}, 'objects': objects}), encoding='utf-8')
    choices = tmp_path / 'choices.csv'
    with choices.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=';'); writer.writeheader(); writer.writerows(rows)
    output = tmp_path / 'CashReceipts.sql'
    db = MagicMock(); db.cursor.return_value.fetchone.return_value = [1]
    @contextmanager
    def connect(*a, **kw): yield db
    command = 'transferdata.erp.control.management.commands.prepare_cash_receipts_report'
    batch = {'objects': [objects[n] for n in (OBJECTS[2], DDS, BUDGET, KINDS)]}
    with patch(command + '.read_metadata', return_value=batch), patch(command + '.connect', connect), patch(command + '.describe', return_value=[1]):
        call_command('prepare_cash_receipts_report', metadata=str(metadata), choices=str(choices), output=str(output), create_job=True,
                     target_connection=job.target_connection.name)
    created = ETLJob.objects.get(target_table='CashReceipts')
    assert not created.schedule_enabled and created.source_type == 'SQL_QUERY' and created.target_schema == 'erp_ai'
    assert created.sql_query == output.read_text().strip()
    assert db.cursor.return_value.execute.call_count == 7
    with pytest.raises(CommandError, match='уже существует'):
        call_command('prepare_cash_receipts_report', output=str(output))
