from django.urls import path, include
from django.views.generic import RedirectView
from transferdata.web.panel.admin import site
from transferdata.web.panel import studio, job_management

urlpatterns = [
    path("", studio.portal, name="etl_portal"),
    path("transferdata/", studio.overview, name="td_overview"),
    path("erp/", include("transferdata.erp.urls")),
    path("runs/", studio.history, name="td_history"),
    path("runs/<int:pk>/", studio.run_detail, name="td_run"),
    path("jobs/", studio.jobs, name="td_jobs"),
    path("groups/", job_management.groups, name="etl_groups"),
    path("jobs/bulk/", job_management.bulk, name="td_bulk"),
    path("jobs/new/", studio.edit, {"kind": "job"}, name="td_job_add"),
    path("jobs/<int:pk>/edit/", studio.edit, {"kind": "job"}, name="td_job_edit"),
    path("jobs/<int:pk>/<str:operation>/", studio.action, name="td_action"),
    path("connections/", studio.connections, name="td_connections"),
    path("connections/new/", studio.edit, {"kind": "connection"}, name="td_connection_add"),
    path("connections/<int:pk>/edit/", studio.edit, {"kind": "connection"}, name="td_connection_edit"),
    path("panel/job/", studio.legacy_jobs),
    path("panel/run/", RedirectView.as_view(pattern_name="td_history")),
    path("panel/connection/", RedirectView.as_view(pattern_name="td_connections")),
    path("", site.urls),
]
