# test_runner.py
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'avito_bidder.settings')
django.setup()

from main_app.tasks import trigger_all_active_tasks

print("!!! --- Импорт прошел успешно! --- !!!")
print("!!! --- Отправляю тестовую задачу... --- !!!")

trigger_all_active_tasks.delay()

print("!!! --- Задача отправлена. Проверяйте логи Celery Worker! --- !!!")
