from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from transferdata.erp.reports.payment_schedule import REGISTER, REGISTRY, POSITION, PART, generate


@pytest.fixture
def payment_metadata():
    objects = {}
    counter = 0
    def p(name, refs=(), dim=False, measure=False):
        nonlocal counter
        counter += 1
        return dict(name=name, references=list(refs), is_dimension=dim, is_measure=measure,
                    columns=[dict(name=f'_column{counter}', purpose='Value', type='numeric(15,2)' if measure else 'binary(16,fixed)')])
    def obj(name, props):
        objects[name] = dict(name=name, table='_table' + str(len(objects)), properties=props,
                             year_offset=2000, configuration='test', version=42,
                             partitions=[{'key': 'shared', 'source': '_Area'}], table_parts=[])
    order, invoice, currency, org, supplier, enum = ['Документ.ЗаказПоставщику', 'Документ.Инвойс', 'Справочник.Валюты', 'Справочник.Организации', 'Справочник.Контрагенты', 'Перечисление.ТипыПлатежей']
    for name in (org, supplier, currency):
        obj(name, [p('Ссылка'), p('Наименование'), p('Код')])
    objects[currency]['properties'].append(p('БуквенныйКод'))
    for name in (order, invoice, POSITION, REGISTRY):
        props = [p('Ссылка'), p('Дата'), p('Номер'), p('Проведен')]
        if name == order:
            props += [p('Организация', [org]), p('Контрагент', [supplier]), p('Валюта', [currency])]
        if name == invoice:
            props += [p('СуммаДокумента')]
        obj(name, props)
    obj(enum, [])
    obj(REGISTER, [p('Период'), p('Активность'), p('ЗаказПоставщику', [order], dim=True),
        p('ТипПлатежа', [enum], dim=True), p('ЗаявкаНаОплату', [POSITION], dim=True), p('Инвойс', [invoice], dim=True),
        p('ЕстьОплата', dim=True), p('ДатаКурса', dim=True), p('СуммаПлатежа', measure=True), p('СуммаУпр', measure=True), p('ДругойРесурс', measure=True)])
    objects[REGISTRY]['table_parts'] = [dict(name='ТаблицаПлатежи', table='_Lines', properties=[p('Ссылка'), p('ПлатежнаяПозиция', [POSITION])],
        partitions=deepcopy(objects[REGISTER]['partitions']))]
    targets = []
    for index, (name, data) in enumerate(objects.items()):
        if not name.startswith(('Документ.', 'Справочник.', 'Перечисление.')):
            continue
        target = dict(name=name, type_code=900+index, table=data['table'], partitions=data['partitions'])
        if name == enum:
            target['values'] = [dict(name="Предоплата 'аванс'", hex='AB'*16)]
        else:
            target['id'] = next(x['columns'][0]['name'] for x in data['properties'] if x['name'] == 'Ссылка')
            for field, key in [('Наименование', 'description'), ('Дата', 'date'), ('Номер', 'number')]:
                target[key] = next((x['columns'][0]['name'] for x in data['properties'] if x['name'] == field), None)
        targets.append(target)
    objects[REGISTER]['reference_targets'] = targets
    return objects


def test_daily_turnover_preserves_dimensions_references_and_statuses(payment_metadata):
    sql = generate(payment_metadata)
    assert 'CONVERT(datetime, CONVERT(date,' in sql
    assert sql.count(' THEN -') == 0  # turnover register has no fabricated expense sign
    assert 'HAVING SUM(' in sql and sql.split('HAVING ', 1)[1].split('\n)', 1)[0].count(' <> 0') == 3
    assert 'EXISTS (SELECT 1' in sql and '[dbo].[_Lines]' in sql
    for status in ['Факт', 'Заявка в реестре', 'План (Заявка создана)', 'План (Заявка не создана)']:
        assert status in sql
    for field in ['ЗаказПоставщикуСсылка', 'ЗаказПоставщикуДата', 'ЗаказПоставщикуНомер', 'ИнвойсСсылка', 'ИнвойсДата', 'ИнвойсНомер', 'СуммаИнвойса', 'ВалютаПлатежаСсылка', 'ОбластьДанных']:
        assert '[' + field + ']' in sql
    assert "Предоплата ''аванс''" in sql
    # No restriction on payment date: all history and future payments, unlike as-of cash receipts.
    assert '[PaymentDay] <' not in sql and 'SYSUTCDATETIME' not in sql


def test_usd_is_ready_management_resource_without_conversion(payment_metadata):
    sql = generate(payment_metadata)
    prop = next(p for p in payment_metadata[REGISTER]['properties'] if p['name'] == 'СуммаУпр')
    column = prop['columns'][0]['name']
    assert f'[t].[{column}] AS [USD]' in sql
    assert "N'USD' AS [ВалютаUSD]" in sql
    assert 'КурсыВалют' not in sql and 'ROUND(' not in sql and 'OUTER APPLY' not in sql
    assert 'AS [Сумма]' in sql


def test_missing_or_ambiguous_management_resource_is_not_fabricated(payment_metadata):
    prop = next(p for p in payment_metadata[REGISTER]['properties'] if p['name'] == 'СуммаУпр')
    prop['name'] = 'ГотоваяУправленческаяСумма'
    with pytest.raises(ValidationError, match='Ресурсы регистра'):
        generate(payment_metadata)
    sql = generate(payment_metadata, usd_resource='ГотоваяУправленческаяСумма')
    assert '[USD]' in sql
    with pytest.raises(ValidationError, match='отдельный ресурс'):
        generate(payment_metadata, usd_resource='СуммаПлатежа')
    prop['name'] = 'СуммаУпр'
    alternative = deepcopy(prop)
    alternative['name'] = 'СуммаПлатежаУпр'
    alternative['columns'][0]['name'] = '_AnotherManagementAmount'
    payment_metadata[REGISTER]['properties'].append(alternative)
    with pytest.raises(ValidationError, match='однозначный'):
        generate(payment_metadata)


def test_metadata_ambiguities_fail_closed(payment_metadata):
    bad = deepcopy(payment_metadata)
    bad[REGISTRY]['table_parts'][0].pop('properties')
    with pytest.raises(ValidationError, match='табличной части'):
        generate(bad)
    bad = deepcopy(payment_metadata)
    bad[REGISTER]['properties'][0].pop('is_dimension')
    with pytest.raises(ValidationError, match='роли'):
        generate(bad)


def test_global_currency_catalogue_and_safe_partition_checks(payment_metadata):
    currency = 'Справочник.Валюты'
    payment_metadata[currency]['partitions'] = []
    for target in payment_metadata[REGISTER]['reference_targets']:
        if target['name'] == currency:
            target['partitions'] = []
    assert '1 = 1' in generate(payment_metadata, usd=True)
    payment_metadata[REGISTRY]['table_parts'][0]['partitions'][0]['key'] = 'different'
    with pytest.raises(ValidationError, match='Разделители'):
        generate(payment_metadata)


@pytest.mark.django_db
def test_command_reads_real_structures_and_creates_manual_job(job, payment_metadata, tmp_path):
    from transferdata.erp.control.models import ETLJob
    source = job.source_connection
    output = tmp_path / 'Payment.sql'
    command = 'transferdata.erp.control.management.commands.prepare_payment_schedule_report'
    root_names = (REGISTER, REGISTRY)
    roots = {'objects': [payment_metadata[n] for n in root_names]}
    related = {'objects': [o for n,o in payment_metadata.items() if n not in root_names]}
    @contextmanager
    def connect(*args, **kwargs): yield MagicMock()
    with patch(command + '.read_metadata', side_effect=[roots, related]) as read, patch(command + '.connect', connect), patch(command + '.describe', return_value=[1]):
        call_command('prepare_payment_schedule_report', connection=source.name, target_connection=job.target_connection.name,
                     output=str(output), create_job=True)
    assert read.call_args_list[0].kwargs['include_table_parts'] is True
    created = ETLJob.objects.get(target_table='PaymentScheduleReport')
    assert created.source_type == 'SQL_QUERY' and not created.schedule_enabled
    assert created.sql_query == output.read_text().strip() and '[USD]' in created.sql_query
