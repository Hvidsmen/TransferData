import os

import pytest
from transferdata.erp.control.models import DatabaseConnection, ETLJob

@pytest.fixture
def job(db):
    source = DatabaseConnection.objects.create(name='ERP', server='source', database='erp', authentication_type='WINDOWS', role='SOURCE')
    target = DatabaseConnection.objects.create(name='DWH', server='target', database='dwh', authentication_type='WINDOWS', role='TARGET')
    return ETLJob.objects.create(name='Products', source_connection=source, target_connection=target, source_table='dbo._Reference1', target_table='Products')

@pytest.fixture
def administrator_user(db, django_user_model):
    return django_user_model.objects.create_superuser(username='admin', password='test-pass-for-tests-only')

@pytest.fixture
def observer(db, django_user_model):
    return django_user_model.objects.create_user(username='viewer', is_staff=True, password='test-pass-for-tests-only')
