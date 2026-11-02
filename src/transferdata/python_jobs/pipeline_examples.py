"""The user's procedure list only; no passwords or execution during setup."""
from django.db import transaction
from django.core.exceptions import ValidationError
from transferdata.web.maintenance import is_paused
from .models import Job


def example_steps(database, specifications):
    return [{'id': key, 'name': title, 'database': database, 'schema': schema, 'procedure': procedure,
             'enabled': True, 'depends_on': dependencies, 'parameters': {}, 'timeout': 1800}
            for key, title, schema, procedure, dependencies in specifications]


@transaction.atomic
def create_examples(connection):
    if is_paused() or not connection.is_active:
        raise ValidationError('Установка недоступна во время обновления или с отключённым подключением.')
    groups = [
        ('Бухгалтерия и ОС', '1С', [
            ('ledger', 'Хозрасчётный регистр', 'dbo', 'sp_РегистрБухгалтерии.Хозрасчетный', []),
            ('assets', 'Амортизация ОС', 'report', 'sp_ВедомостьАрмотизацииОС', ['ledger']),
            ('assets_period', 'Амортизация ОС по периодам', 'report', 'sp_ВедомостьАрмотизацииОСПериод', ['assets'])]),
        ('Обновление DataWH', 'DataWH', [
            ('vacation', 'Отчёт по отпускам', 'dbo', 'sp_ADDOtpuskReoport', []),
            ('vacation_fast', 'Обновление итогового отчёта по отпускам', 'dbo', 'sp_RefreshAddOtpuskReportFast', ['vacation']),
            ('stock', 'Остатки по счёту 10', 'dbo', 'sp_StockOnSchet10', []),
            ('references', 'Обновление справочников', 'BP', 'sp_UpdateREF', []),
            ('zup', 'Обновление ЗУП', 'zup', 'UpdateTables', []),
            ('fact', 'БДР: факт', 'bdr', 'sp_Fact', []),
            ('plan_fact', 'БДР: план-факт', 'bdr', 'SP_PlanFactReport', ['fact'])])]
    if Job.objects.filter(name__in=[g[0] for g in groups]).exists():
        raise ValidationError('Задания с такими названиями уже существуют. Дубликаты не созданы.')
    created = []
    for name, database, specifications in groups:
        job = Job(name=name, handler='sql_pipeline', connection=connection, transaction_mode='atomic',
                  steps=example_steps(database, specifications), timeout=21600, enabled=False)
        job.full_clean(); job.save(); created.append(job)
    return created
