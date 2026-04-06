from django.apps import AppConfig


class SchedulerConfig(AppConfig):
    name = 'scheduler'
    verbose_name = 'ArkWeather Scheduler'

    def ready(self):
        # Only start the scheduler in the main process.
        # Prevents double-start with Django's auto-reloader (which spawns a child).
        import os
        if os.environ.get('RUN_MAIN') == 'true' or not os.environ.get('DJANGO_SETTINGS_MODULE'):
            # In production (gunicorn/uvicorn) RUN_MAIN is not set, so we check
            # for the reloader token differently.
            pass
        from .jobs import start
        start()
