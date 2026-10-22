from django import forms
from .models import DatabaseConnection, ETLJob
from transferdata.web.schedule_widget import ScheduleWidget
from transferdata.scheduling import simple_schedule

class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'

class ConnectionForm(StyledForm):
    class Meta:
        model = DatabaseConnection
        exclude = ['created_at', 'updated_at']

class JobForm(StyledForm):
    class Meta:
        model = ETLJob
        exclude = ['created_at', 'updated_at', 'created_by']
        widgets = {'schedule_time': forms.HiddenInput(), 'schedule_cron': ScheduleWidget(advanced=False),
                   'sql_query': forms.Textarea(attrs={'rows': 10, 'class': 'form-control font-monospace'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['schedule_enabled'].label = 'Включить расписание'
        self.fields['schedule_cron'].help_text = 'Время — в часовом поясе портала (по умолчанию Europe/Moscow).'
        if not self.is_bound and not self.instance.schedule_cron:
            at = self.instance.schedule_time
            self.initial['schedule_cron'] = f'{at.minute} {at.hour} * * *' if at else '0 2 * * *'

    def clean(self):
        data = super().clean()
        if data.get('schedule_cron'):
            try:
                data['schedule_time'] = simple_schedule(data['schedule_cron'])['time']
            except ValueError as exc:
                self.add_error('schedule_cron', str(exc))
        return data
