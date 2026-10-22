from django import forms
from django.conf import settings


class ScheduleWidget(forms.TextInput):
    template_name = "panel/schedule.html"

    def __init__(self, attrs=None, advanced=True):
        super().__init__(attrs)
        self.advanced = advanced

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context.update(advanced=self.advanced, month_days=range(1, 32), portal_timezone=settings.TIME_ZONE,
                       weekdays=enumerate(["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]))
        return context

    class Media:
        js = ["panel/schedule.js"]
        css = {"all": ["panel/schedule.css"]}
