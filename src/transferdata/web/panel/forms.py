import json
from django import forms
from .models import Job, Run
from transferdata.web.schedule_widget import ScheduleWidget


class MappingWidget(forms.Textarea):
    template_name = "panel/mapping.html"

    class Media:
        js = ["panel/mapping.js"]


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = "__all__"
        widgets = {"columns": MappingWidget(attrs={"rows": 6, "cols": 70}), "cron": ScheduleWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cron"].label = "Расписание"
        self.fields["cron"].help_text = "Расписание применяется, когда включён переключатель «Включить расписание» и работает обработчик."
        if self.instance.pk:
            metadata = Run.objects.filter(job=self.instance, action="inspect", status="success").first()
            if metadata:
                self.fields["source_table"].widget.attrs["data-tables"] = json.dumps(
                    metadata.details.get("tables", []), ensure_ascii=True)
                self.fields["columns"].widget.attrs["data-columns"] = json.dumps(
                    metadata.details.get("columns", []), ensure_ascii=True)
