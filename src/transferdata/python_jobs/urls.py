from django.urls import path
from transferdata.web.panel import job_management
from . import views
app_name = 'python_jobs'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('jobs/', views.jobs, name='jobs'),
    path('jobs/steps/examples/', views.pipeline_examples, name='pipeline_examples'),
    path('jobs/new/', views.edit, name='add'),
    path('jobs/<int:pk>/edit/', views.edit, name='edit'),
    path('jobs/<int:pk>/run/', views.run_job, name='execute'),
    path('jobs/bulk/', job_management.bulk, {'subsystem': 'python'}, name='bulk'),
    path('jobs/<int:pk>/steps/start/', views.pipeline_start, name='pipeline_start'),
    path('runs/<int:pk>/stop/', views.stop, name='stop'),
    path('runs/<int:pk>/resume/', views.resume, name='resume'),
    path('runs/', views.history, name='history'),
    path('runs/<int:pk>/', views.detail, name='run'),
]
