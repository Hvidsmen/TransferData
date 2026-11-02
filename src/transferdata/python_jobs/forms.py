from django import forms
from django.conf import settings
from transferdata.web.schedule_widget import ScheduleWidget
from transferdata.erp.control.models import DatabaseConnection
from .models import Job


class StepsWidget(forms.Textarea):
    template_name = 'python_jobs/steps_widget.html'

    class Media:
        js = ['panel/pipeline-editor.js']


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = ['name', 'group', 'handler', 'connection', 'script', 'parameters', 'steps', 'transaction_mode', 'timeout', 'enabled', 'cron', 'timezone']
        widgets = {'steps': StepsWidget(), 'cron': ScheduleWidget(), 'parameters': forms.Textarea(attrs={'rows': 4, 'spellcheck': 'false'}),
                   'group': forms.Select(attrs={'data-searchable': 'true'}), 'connection': forms.Select(attrs={'data-searchable': 'true'})}

    class Media:
        js = ['panel/python-jobs.js']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.DEPLOYMENT['profile'] == 'yds':
            self.fields['handler'].choices = [('script', 'Python-файл на сервере'), ('sql_pipeline', 'Цепочка SQL-процедур')]
            self.fields['parameters'].help_text = 'JSON-объект передаётся скрипту через ETL_JOB_PARAMETERS.'
            if not self.instance.pk:
                self.initial['handler'] = 'script'
        self.fields['transaction_mode'].help_text = 'Одна транзакция сохраняет прежнюю логику группы. Фиксация каждого шага позволяет продолжение с середины, но меняет поведение при ошибке. Внутренние COMMIT процедур портал не отменяет.'
        self.fields['connection'].queryset = DatabaseConnection.objects.filter(is_active=True).order_by('name')
