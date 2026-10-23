from django.urls import path
from transferdata.web.panel import job_management
from . import views
app_name = 'python_jobs'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('jobs/', views.jobs, name='jobs'),
    path('jobs/new/', views.edit, name='add'),
    path('jobs/<int:pk>/edit/', views.edit, name='edit'),
    path('jobs/<int:pk>/run/', views.run_job, name='execute'),
    path('jobs/bulk/', job_management.bulk, {'subsystem': 'python'}, name='bulk'),
    path('runs/', views.history, name='history'),
    path('runs/<int:pk>/', views.detail, name='run'),
]
