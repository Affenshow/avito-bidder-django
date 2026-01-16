# main_app/apps.py
from django.apps import AppConfig

class MainAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'main_app'

    def ready(self):
        # Импортируем сигналы, чтобы Django о них узнал
        # noinspection PyUnresolvedReferences
        import main_app.models 