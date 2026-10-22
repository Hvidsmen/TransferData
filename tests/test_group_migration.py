import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_string_groups_become_one_shared_catalogue_without_losing_jobs():
    old = [('panel', '0002_job_group_job_job_type_job_procedure_name_and_more'),
           ('control', '0004_etljob_group')]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        Connection = apps.get_model('panel', 'Connection')
        Job = apps.get_model('panel', 'Job')
        ERPConnection = apps.get_model('control', 'DatabaseConnection')
        ERPJob = apps.get_model('control', 'ETLJob')
        c = Connection.objects.create(name='test')
        job = Job.objects.create(name='table', connection=c, group='Общая')
        ungrouped = Job.objects.create(name='empty', connection=c, group='')
        ec = ERPConnection.objects.create(name='erp', server='fake', database='fake')
        erp = ERPJob.objects.create(name='erp job', source_connection=ec, target_connection=ec, group='Общая')
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        group = apps.get_model('panel', 'JobGroup').objects.get(name='Общая')
        assert apps.get_model('panel', 'Job').objects.get(pk=job.pk).group_id == group.pk
        assert apps.get_model('control', 'ETLJob').objects.get(pk=erp.pk).group_id == group.pk
        assert apps.get_model('panel', 'Job').objects.get(pk=ungrouped.pk).group_id is None
    finally:
        MigrationExecutor(connection).migrate(latest)
