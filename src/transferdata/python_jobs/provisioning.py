"""Seed approved YDS tasks only after the service database schema is ready."""
from django.conf import settings
from django.core.management import call_command
from django.db import connections


def provision_yds(sender, using='default', plan=None, **kwargs):
    if settings.DEPLOYMENT['profile'] != 'yds' or using != 'default' or any(backwards for _, backwards in (plan or [])):
        return
    # Also respect explicit migration targets which precede the provisioning column.
    from .models import Job
    with connections[using].cursor() as cursor:
        if Job._meta.db_table not in connections[using].introspection.table_names(cursor):
            return
        columns = connections[using].introspection.get_table_description(cursor, Job._meta.db_table)
    required = {field.column for field in Job._meta.local_concrete_fields}
    if not required.issubset({column.name for column in columns}):
        return
    call_command('setup_sql_pipelines')
