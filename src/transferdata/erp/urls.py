app_name = "erp"
from django.contrib import admin
from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from transferdata.erp.control import views as v
from transferdata.erp.control import onec_views
from transferdata.web.panel import job_management
urlpatterns = [
    path('onec/', onec_views.catalogue, name='onec_catalogue'),
    path('onec/options/', onec_views.options, name='onec_options'),
    path('onec/objects/<int:pk>/', onec_views.detail, name='onec_detail'),
    path('onec/connections/<int:pk>/refresh/', onec_views.refresh, name='onec_refresh'),
    path('', v.dashboard, name='dashboard'), path('connections/', v.connections, name='connections'),
    path('connections/new/', v.edit, {'kind': 'connection'}, name='connection_create'),
    path('connections/<int:pk>/edit/', v.edit, {'kind': 'connection'}, name='connection_edit'),
    path('connections/<int:pk>/test/', v.connection_test, name='connection_test'),
    path('etl/jobs/', v.jobs, name='jobs'), path('etl/jobs/new/', v.edit, {'kind': 'job'}, name='job_create'),
    path('etl/jobs/bulk/', job_management.bulk, {'subsystem': 'erp'}, name='job_bulk'),
    path('etl/jobs/<int:pk>/', v.job_detail, name='job_detail'),
    path('etl/jobs/<int:pk>/edit/', v.edit, {'kind': 'job'}, name='job_edit'),
    path('etl/jobs/<int:pk>/run/', v.job_run, name='job_run'),
    path('etl/jobs/<int:pk>/disable/', v.job_disable, name='job_disable'),
    path('etl/executions/', v.executions, name='executions'),
    path('etl/executions/<int:pk>/cancel/', v.execution_cancel, name='execution_cancel'),
    path('etl/executions/<int:pk>/', v.execution_detail, name='execution_detail'),
]
