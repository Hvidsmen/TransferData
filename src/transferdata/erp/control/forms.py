from django import forms
from .models import DatabaseConnection, ETLJob

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
        widgets = {'schedule_time': forms.TimeInput(attrs={'type': 'time'}), 'sql_query': forms.Textarea(attrs={'rows': 10, 'class': 'form-control font-monospace'})}
