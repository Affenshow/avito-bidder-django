# avito_bidder/celery.py
import os
from celery import Celery

# Устанавливаем переменную окружения, чтобы Celery знал, где искать настройки Django.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'avito_bidder.settings.local')

app = Celery('avito_bidder')

# Celery будет использовать префикс 'CELERY' для своих настроек в settings.py
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически находить и подхватывать задачи из файлов tasks.py в наших приложениях.
app.autodiscover_tasks()
