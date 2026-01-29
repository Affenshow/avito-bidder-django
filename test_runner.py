# test_runner.py (ИСПРАВЛЕННАЯ ВЕРСИЯ)
import os
import django

def main():
    """
    Основная функция. Сначала настраиваем Django, потом импортируем.
    """
    # 1. Настраиваем окружение и запускаем Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'avito_bidder.settings.local')
    django.setup()

    # 2. ИМПОРТИРУЕМ НАШИ МОДЕЛИ/ЗАДАЧИ ТОЛЬКО ПОСЛЕ django.setup()
    from main_app.tasks import trigger_all_active_tasks

    print("!!! --- Импорт прошел успешно! --- !!!")
    print("!!! --- Отправляю тестовую задачу... --- !!!")

    trigger_all_active_tasks.delay()

    print("!!! --- Задача отправлена. Проверяйте логи Celery Worker! --- !!!")


if __name__ == '__main__':
    main()
