import json
from unittest.mock import patch

import pytest
from django.core.management import call_command, CommandError

from transferdata.erp.reports import cash_receipts

pytestmark = pytest.mark.django_db


def test_report_metadata_exports_nested_objects_but_no_secrets_or_mutations(job, tmp_path):
    def metadata(connection, name=None, *, names=None):
        if names is not None:
            return {'objects': [metadata(connection, value) for value in names]}
        return {'name': name, 'table': '_Example', 'properties': [
            {'name': 'Контрагент', 'references': ['Справочник.Контрагенты'], 'columns': []},
            {'name': 'Партнер', 'references': ['Справочник.Партнеры'], 'columns': []},
            {'name': 'Наименование', 'columns': []},
        ]}
    output = tmp_path / 'report.json'
    with patch.object(cash_receipts, 'OBJECTS', ['Документ.ПоступлениеБезналичныхДенежныхСредств']), \
         patch.object(cash_receipts, 'PATHS', [('Документ.ПоступлениеБезналичныхДенежныхСредств', ['Контрагент', 'Партнер', 'Наименование'])]), \
         patch('transferdata.erp.control.management.commands.export_onec_report_metadata.read_metadata', side_effect=metadata) as helper:
        call_command('export_onec_report_metadata', output=str(output))
    data = json.loads(output.read_text())
    assert set(data['objects']) == {'Документ.ПоступлениеБезналичныхДенежныхСредств', 'Справочник.Контрагенты', 'Справочник.Партнеры'}
    assert not data['issues'] and helper.call_count == 3
    assert data['source'] == {'connection': job.source_connection.name, 'database': job.source_connection.database}
    assert 'password' not in output.read_text() and 'username' not in output.read_text()
    assert not job.source_connection.onec_objects.exists()
    assert not job.executions.exists()
    with pytest.raises(CommandError, match='уже существует'):
        call_command('export_onec_report_metadata', connection=job.source_connection.name, output=str(output))


def test_report_metadata_reports_unresolved_types_and_missing_fields(job, tmp_path):
    with patch.object(cash_receipts, 'OBJECTS', ['Документ.Тест']), \
         patch.object(cash_receipts, 'PATHS', [('Документ.Тест', ['Контрагент', 'Наименование']), ('Документ.Тест', ['Отсутствует'])]), \
         patch('transferdata.erp.control.management.commands.export_onec_report_metadata.read_metadata', return_value={
             'objects': [], 'name': 'Документ.Тест', 'properties': [{'name': 'Контрагент', 'references': ['ЛюбаяСсылка']}]}):
        output = tmp_path / 'issues.json'
        call_command('export_onec_report_metadata', connection=job.source_connection.name, output=str(output))
    assert len(json.loads(output.read_text())['issues']) == 2
