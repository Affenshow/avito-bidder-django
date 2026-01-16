# main_app/tasks.py

import logging
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from typing import Union
from .models import BiddingTask, TaskLog


logger = logging.getLogger(__name__)

# =================================================================
# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (с учетом user_api_keys) ==========
# =================================================================

def get_ad_position(search_url: str, ad_id: int) -> Union[int, None]:
    """
    Парсит страницу Avito и возвращает позицию объявления.
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()

        with open("debug_avito_page.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        logger.info("!!! HTML-страница УСПЕШНО сохранена в файл debug_avito_page.html !!!")

        soup = BeautifulSoup(response.text, 'html.parser')
        all_ads = soup.find_all('div', {'data-marker': 'item'})
        
        if not all_ads:
            logger.warning("Парсер: Не найдено ни одного объявления на странице (возможно, капча или пустая страница).")
            return None

        for index, ad_element in enumerate(all_ads):
            if ad_element.get('data-item-id') == str(ad_id):
                logger.info(f"Парсер: Найдена позиция {index + 1} для ad_id={ad_id}")
                return index + 1
        
        logger.warning(f"Парсер: Объявление ad_id={ad_id} не найдено на странице.")
        return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"[ПАРСЕР] Ошибка HTTP-запроса: {e.response.status_code} {e.response.reason}")
        with open("debug_ERROR_page.html", "w", encoding="utf-8") as f:
            f.write(e.response.text)
        logger.info("!!! Страница с ошибкой сохранена в debug_ERROR_page.html !!!")
        return None
    except Exception as e:
        logger.error(f"[ПАРСЕР] Непредвиденная ошибка: {e}")
        return None

def get_current_ad_price(ad_id: int, user_api_keys: dict) -> Union[float, None]:
    """ЗАГЛУШКА: Получает текущую цену через API Avito."""
    logger.info(f"[API-ЗАГЛУШКА] Получаем текущую цену для ad_id={ad_id} с client_id={user_api_keys.get('client_id')}")
    # Здесь в будущем будет реальный GET-запрос к API Avito
    return 25.0

def set_ad_price(ad_id: int, new_price: float, user_api_keys: dict) -> bool:
    """ЗАГЛУШКА: Устанавливает новую цену через API Avito."""
    logger.info(f"[API-ЗАГЛУШКА] Устанавливаем цену {new_price} для ad_id={ad_id} с client_id={user_api_keys.get('client_id')}")
    # Здесь в будущем будет реальный POST/PUT-запрос к API Avito
    return True

# =================================================================
# === ОСНОВНАЯ ЗАДАЧА CELERY (с интеграцией API-ключей) ==========
# =================================================================

# main_app/tasks.py

@shared_task
def run_bidding_for_task(task_id: int):
    """Основная логика биддера с записью логов в базу данных."""
    task = None # Инициализируем переменную
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
        TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")
    except BiddingTask.DoesNotExist:
        # Здесь мы не можем создать лог, так как не знаем, к какой задаче он относится
        logger.warning(f"Задача #{task_id} не найдена или неактивна. Пропускаем.")
        return
    except Exception as e:
        logger.error(f"!!! КРИТИЧЕСКАЯ ОШИБКА при получении задачи из БД: {e} !!!")
        if task:
            TaskLog.objects.create(task=task, message=f"Критическая ошибка: {e}", level='ERROR')
        return

    # 1. Получаем профиль и ключи
    profile = task.user.profile
    if not profile.avito_client_id or not profile.avito_client_secret:
        TaskLog.objects.create(task=task, message="API-ключи не настроены. Задача пропущена.", level='ERROR')
        return

    user_api_keys = {'client_id': profile.avito_client_id, 'client_secret': profile.avito_client_secret}

    # 2. Получаем позицию
    position = get_ad_position(task.search_url, task.ad_id)
    if position is None:
        TaskLog.objects.create(task=task, message="Не удалось получить позицию объявления.", level='ERROR')
        return

    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: <= {task.target_position}.")

    # 3. Получаем цену
    current_price = get_current_ad_price(task.ad_id, user_api_keys)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену (заглушка API).", level='ERROR')
        return

    # 4. Алгоритм
    if position > task.target_position:
        new_price = float(current_price) + 1.0
        if new_price <= float(task.max_price):
            set_ad_price(task.ad_id, new_price, user_api_keys)
            TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position}. Ставка повышена до {new_price} ₽.", level='WARNING')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута максимальная ставка {task.max_price} ₽. Ставка не изменена.", level='WARNING')
    else:
        TaskLog.objects.create(task=task, message="Позиция в норме. Ставка не изменена.")

    TaskLog.objects.create(task=task, message="Биддер завершил работу.")


# ===============================================================
# === ЗАДАЧА-ТРИГГЕР (без изменений) ============================
# ===============================================================

@shared_task
def trigger_all_active_tasks():
    """Находит все активные задачи в БД и запускает для каждой из них биддер."""
    logger.info(">>> ПЛАНИРОВЩИК: Поиск активных задач...")
    active_tasks = BiddingTask.objects.filter(is_active=True)
    task_count = active_tasks.count()
    if task_count == 0:
        logger.info(">>> ПЛАНИРОВЩИК: Активных задач не найдено.")
        return

    logger.info(f">>> ПЛАНИРОВЩИК: Найдено {task_count} задач. Запуск...")
    for task in active_tasks:
        run_bidding_for_task.delay(task.id)
    logger.info(f">>> ПЛАНИРОВЩИК: Завершено. Запущено {task_count} задач. <<<")
