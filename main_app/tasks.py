# main_app/tasks.py
import logging
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from typing import Union, List
from datetime import datetime
import json

from .models import BiddingTask, TaskLog
from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price

logger = logging.getLogger(__name__)


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_ad_position(search_url: str, ad_id: int) -> Union[int, None]:
    """Парсит страницу Avito и возвращает позицию объявления."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        all_ads = soup.find_all('div', {'data-marker': 'item'})
        if not all_ads:
            logger.warning(f"Парсер: Не найдено объявлений на странице {search_url}")
            return None
        for index, ad_element in enumerate(all_ads):
            if ad_element.get('data-item-id') == str(ad_id):
                return index + 1
        return None
    except Exception as e:
        logger.error(f"Парсер: Ошибка при обработке URL {search_url}. Ошибка: {e}")
        return None


def is_time_in_schedule(schedule: List[dict]) -> bool:
    """Проверяет, входит ли текущее время в один из интервалов расписания."""
    if not schedule:
        return True  # Если расписание пустое, считаем, что работает всегда
    
    now = datetime.now().time()
    for interval in schedule:
        try:
            start = datetime.strptime(interval.get("start"), "%H:%M").time()
            end = datetime.strptime(interval.get("end"), "%H:%M").time()
            # Проверяем, если интервал переходит через полночь
            if start <= end:
                if start <= now < end:
                    return True
            else:  # Интервал типа 22:00 - 02:00
                if start <= now or now < end:
                    return True
        except (ValueError, TypeError):
            continue  # Игнорируем неправильно отформатированные интервалы
    return False


# --- ОСНОВНАЯ ЗАДАЧА CELERY ---
@shared_task
def run_bidding_for_task(task_id: int):
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.warning(f"Задача #{task_id} больше не существует или неактивна.")
        return

    # 1. Проверяем расписание
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача #{task.id} неактивна по расписанию. Пропускаем.")
        return

    TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")

    # 2. Получаем токен
    profile = task.user.profile
    if not profile.avito_client_id or not profile.avito_client_secret:
        TaskLog.objects.create(task=task, message="API-ключи не настроены. Пропуск.", level='ERROR')
        return
    
    access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
    if not access_token:
        TaskLog.objects.create(task=task, message="Не удалось получить токен доступа.", level='ERROR')
        return

    # 3. Получаем позицию
    position = get_ad_position(task.search_url, task.ad_id)
    if position is None:
        TaskLog.objects.create(task=task, message="Не удалось получить позицию объявления.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: <= {task.target_position}.")

    # 4. Получаем реальную цену
    current_price = get_current_ad_price(task.ad_id, access_token)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

    # 5. "Умный" алгоритм
    if position > task.target_position:
        # --- ЛОГИКА ПОВЫШЕНИЯ ---
        new_price = float(current_price) + float(task.bid_step)
        if new_price <= float(task.max_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position}. Ставка повышена до {new_price} ₽.", level='WARNING')
            else:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position}. НЕ УДАЛОСЬ повысить ставку до {new_price} ₽.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута макс. ставка {task.max_price} ₽. Ставка не повышена.", level='WARNING')

    elif position <= (task.target_position - 5): # "Буфер" в 5 позиций
        # --- ЛОГИКА ПОНИЖЕНИЯ ---
        new_price = float(current_price) - float(task.bid_step)
        if new_price >= float(task.min_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Экономия: позиция {position} высокая. Ставка понижена до {new_price} ₽.", level='INFO')
            else:
                TaskLog.objects.create(task=task, message=f"Экономия: НЕ УДАЛОСЬ понизить ставку до {new_price} ₽.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута мин. ставка {task.min_price} ₽.", level='INFO')
    
    else:
        TaskLog.objects.create(task=task, message="Позиция в норме. Ставка не изменена.")

    TaskLog.objects.create(task=task, message="Биддер завершил работу.")



def is_time_in_schedule(schedule_data) -> bool: # Переименуем аргумент для ясности
    """Проверяет, входит ли текущее время в один из интервалов расписания."""
    
    schedule = []
    # --- НАЧАЛО ИСПРАВЛЕНИЯ ---
    if isinstance(schedule_data, str):
        try:
            # Если это строка, пытаемся ее распарсить как JSON
            schedule = json.loads(schedule_data)
        except json.JSONDecodeError:
            # Если это невалидный JSON, считаем расписание пустым
            schedule = []
    elif isinstance(schedule_data, list):
        # Если это уже список, просто используем его
        schedule = schedule_data
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    if not schedule:
        return True  # Если расписание пустое, работает всегда
    
    now = datetime.now().time()
    for interval in schedule:
        # `interval` теперь точно словарь
        try:
            start = datetime.strptime(interval.get("start"), "%H:%M").time()
            end = datetime.strptime(interval.get("end"), "%H:%M").time()
            if start <= end:
                if start <= now < end:
                    return True
            else: 
                if start <= now or now < end:
                    return True
        except (ValueError, TypeError):
            continue
    return False


# --- ЗАДАЧА-ТРИГГЕР (без изменений) ---
@shared_task
def trigger_all_active_tasks():
    logger.info(">>> ПЛАНИРОВЩИК: Поиск активных задач...")
    active_tasks = BiddingTask.objects.filter(is_active=True)
    for task in active_tasks:
        run_bidding_for_task.delay(task.id)
    logger.info(f">>> ПЛАНИРОВЩИК: Запущено {active_tasks.count()} задач.")

