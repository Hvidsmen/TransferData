from django.apps import AppConfig


class PythonJobsConfig(AppConfig):
    name = 'transferdata.python_jobs'
    verbose_name = 'Python обработчик'

    def ready(self):
        from django.db.models.signals import post_migrate
        from .provisioning import provision_yds
        post_migrate.connect(provision_yds, sender=self, dispatch_uid='python_jobs.provision_yds')
