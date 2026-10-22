from django import forms
from .models import DatabaseConnection, ETLJob
from transferdata.web.schedule_widget import ScheduleWidget
from transferdata.scheduling import simple_schedule
from .models import OneCObject

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
    class Media:
        js = ['erp/onec-form.js']

    class Meta:
        model = ETLJob
        exclude = ['created_at', 'updated_at', 'created_by']
        widgets = {'schedule_time': forms.HiddenInput(), 'schedule_cron': ScheduleWidget(advanced=False),
                   'sql_query': forms.Textarea(attrs={'rows': 10, 'class': 'form-control font-monospace'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.urls import reverse
        connection_id = self.data.get('source_connection') if self.is_bound else self.initial.get('source_connection', self.instance.source_connection_id)
        objects = OneCObject.objects.filter(is_active=True, connection__is_active=True).exclude(metadata={})
        self.fields['one_c_object'].queryset = objects.filter(connection_id=connection_id) if str(connection_id or '').isdecimal() else objects.none()
        self.fields['one_c_object'].empty_label = 'Выберите объект 1С'
        self.fields['one_c_object'].widget.attrs.update({'data-searchable': 'true',
            'data-search-placeholder': 'Найти объект 1С…', 'data-empty-message': 'Нет загруженных объектов. Прочитайте структуру в разделе «Метаданные 1С».',
            'data-objects-url': reverse('erp:onec_options')})
        self.fields['group'].widget.attrs['data-searchable'] = 'true'
        self.fields['schedule_enabled'].label = 'Включить расписание'
        self.fields['schedule_cron'].help_text = 'Время — в часовом поясе портала (по умолчанию Europe/Moscow).'
        if not self.is_bound and not self.instance.schedule_cron:
            at = self.instance.schedule_time
            self.initial['schedule_cron'] = f'{at.minute} {at.hour} * * *' if at else '0 2 * * *'

    def clean(self):
        data = super().clean()
        if data.get('source_type') == 'ONEC' and data.get('one_c_object') and data.get('source_connection'):
            from .onec import validate_object
            try:
                self.instance.one_c_mapping = validate_object(data['one_c_object'], data['source_connection'])
            except forms.ValidationError as exc:
                self.add_error('one_c_object', exc.messages)
        elif data.get('source_type') != 'ONEC':
            self.instance.one_c_mapping = {}
        if data.get('schedule_cron'):
            try:
                data['schedule_time'] = simple_schedule(data['schedule_cron'])['time']
            except ValueError as exc:
                self.add_error('schedule_cron', str(exc))
        return data
