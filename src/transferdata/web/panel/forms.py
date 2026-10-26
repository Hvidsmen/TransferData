import json
from django import forms
from .models import Job, Run
from transferdata.web.schedule_widget import ScheduleWidget


class MappingWidget(forms.Textarea):
    template_name = "panel/mapping.html"

    class Media:
        js = ["panel/mapping.js"]


class JobForm(forms.ModelForm):
    class Media:
        js = ["panel/job-type.js"]

    class Meta:
        model = Job
        fields = "__all__"
        widgets = {"columns": MappingWidget(attrs={"rows": 6, "cols": 70}), "cron": ScheduleWidget()}

    def __init__(self, *args, **kwargs):
        if args and args[0] is not None:
            data = args[0].copy()
            data.setdefault("job_type", "transfer")
            data.setdefault("procedure_timeout", "3600")
            data.setdefault("procedure_schema", "dbo")
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        kind = self.data.get("job_type", self.instance.job_type) if self.is_bound else self.instance.job_type
        transfer_fields = ("source_schema", "source_table", "target_schema", "target_table", "batch_size", "allow_empty", "remove_nul_characters", "columns")
        procedure_fields = ("procedure_schema", "procedure_name", "procedure_parameters", "procedure_timeout")
        if self.is_bound:
            for name in transfer_fields if kind == "procedure" else procedure_fields:
                self.fields[name].disabled = True
        self.fields["procedure_parameters"].widget = forms.Textarea(attrs={"rows": 5})
        self.fields["group"].widget.attrs["data-searchable"] = "true"
        self.fields["cron"].label = "Расписание"
        self.fields["cron"].help_text = "Расписание применяется, когда включён переключатель «Включить расписание» и работает обработчик."
        if self.instance.pk:
            metadata = Run.objects.filter(job=self.instance, action__in=["inspect", "prepare"], status="success").first()
            if metadata:
                previous = metadata.snapshot.get('jobs', [{}])[0]
                current = self.instance.config()
                if metadata.snapshot.get('source') != current.get('source') or any(
                    previous.get(key) != current['jobs'][0].get(key) for key in ('source_schema', 'source_table')):
                    metadata = None
            if metadata:
                self.fields["source_table"].widget.attrs["data-tables"] = json.dumps(
                    metadata.details.get("tables", []), ensure_ascii=True)
                self.fields["columns"].widget.attrs["data-columns"] = json.dumps(
                    metadata.details.get("columns", []), ensure_ascii=True)
