from django.urls import path
from transferdata.web.panel.admin import site

urlpatterns = [path("", site.urls)]
