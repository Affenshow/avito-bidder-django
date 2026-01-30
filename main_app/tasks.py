# main_app/tasks.py
import logging
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from typing import Union, List, Dict
from datetime import datetime
import json
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService # Переименуем, чтобы не путаться
from webdriver_manager.chrome import ChromeDriverManager # <-- НАШ НОВЫЙ ИНСТРУМЕНТ
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from .models import BiddingTask, TaskLog
from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price

logger = logging.getLogger(__name__)


def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Парсит страницу с помощью undetected-chromedriver,
    принудительно используя chromedriver 144-й версии.
    """
    
    # --- Настройки Chrome убраны, так как uc настраивает их сам ---
    
    driver = None
    try:
        logger.info("--- [SELENIUM-UC] Запуск Undetected Chrome...")
        
        # --- ГЛАВНОЕ ИЗМЕНЕНИЕ ---
        # Мы используем uc.Chrome и явно указываем ему версию
        driver = uc.Chrome(
            headless=True, 
            use_subprocess=False,
            version_main=144 # <-- ЯВНО УКАЗЫВАЕМ ВЕРСИЮ
        )
        
        # Устанавливаем таймаут ожидания загрузки страницы
        driver.set_page_load_timeout(30)
        
        logger.info(f"--- [SELENIUM-UC] Переход по URL: {search_url} ---")
        driver.get(search_url)
        
        driver.save_screenshot("debug_selenium_page.png")
        logger.info("--- [SELENIUM-UC] Скриншот страницы сохранен.")

        # --- НОВЫЙ БЛОК ДИАГНОСТИКИ ---
        with open("debug_selenium_source.html", "w", encoding="utf-8") as f:
          f.write(driver.page_source)
        logger.info("--- [SELENIUM] Исходный код страницы сохранен в debug_selenium_source.html ---")
    # --- КОНЕЦ БЛОКА ---

        # Ищем все блоки объявлений
        all_ads = driver.find_elements(By.CSS_SELECTOR, "div[data-marker='item']")
        logger.info(f"--- [SELENIUM-UC] Найдено {len(all_ads)} объявлений на странице.")

        if not all_ads:
            return None

        for index, ad_element in enumerate(all_ads):
            if ad_element.get_attribute('data-item-id') == str(ad_id):
                position = index + 1
                title = "Название не найдено"
                image_url = None
                
                try:
                    title_tag = ad_element.find_element(By.CSS_SELECTOR, "a[data-marker='item-title']")
                    title = title_tag.text
                except Exception as e:
                    logger.warning(f"Не удалось найти заголовок: {e}")

                try:
                    img_tag = ad_element.find_element(By.TAG_NAME, "img")
                    image_url = img_tag.get_attribute('src')
                except Exception:
                     logger.warning("Не удалось найти картинку по тегу <img>")
                
                logger.info(f"--- [SELENIUM-UC] Найдено объявление {ad_id} на позиции {position}! ---")
                return {"position": position, "title": title, "image_url": image_url}
        
        logger.warning(f"--- [SELENIUM-UC] Объявление {ad_id} НЕ найдено на странице.")
        return None
    
    except Exception as e:
        logger.error(f"--- [SELENIUM-UC] КРИТИЧЕСКАЯ ОШИБКА: {e} ---")
        # Если есть скриншот ошибки, это очень полезно
        if driver:
            driver.save_screenshot("debug_ERROR_selenium.png")
            logger.info("--- [SELENIUM-UC] Скриншот ОШИБКИ сохранен.")
        return None
        
    finally:
        if driver:
            driver.quit()
            logger.info("--- [SELENIUM-UC] Драйвер Chrome закрыт.")


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
    """
    Основная, полностью рабочая логика биддера.
    """
    # --- 1. Получаем задачу и проверяем, активна ли она ---
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.warning(f"Задача #{task_id} больше не существует или выключена. Пропускаем.")
        return

    # --- 2. Проверяем, активна ли задача по расписанию ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача #{task_id} неактивна по расписанию. Пропускаем.")
        return

    TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")

    # --- 3. Получаем токен доступа к API ---
    profile = task.user.profile
    if not profile.avito_client_id or not profile.avito_client_secret:
        TaskLog.objects.create(task=task, message="API-ключи не настроены. Пропуск.", level='ERROR')
        return
    
    access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
    if not access_token:
        TaskLog.objects.create(task=task, message="Не удалось получить токен доступа. Проверьте API-ключи.", level='ERROR')
        return

    # --- 4. Получаем актуальную информацию с Avito (позиция, title, image) ---
    ad_data = get_ad_position(task.search_url, task.ad_id)
    
    if ad_data is None:
        TaskLog.objects.create(task=task, message="Не удалось получить информацию с Avito (ошибка парсера).", level='ERROR')
        return
    
    position = ad_data.get("position")
    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: [{task.target_position_min} - {task.target_position_max}].")

    # --- 5. Получаем реальную текущую цену через API ---
    current_price = get_current_ad_price(task.ad_id, access_token)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену через API.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

    # --- 6. "Умный" алгоритм биддера ---
    # Если мы НИЖЕ нашего диапазона (например, на 15-м месте, а цель 5-10)
    if position > task.target_position_max:
        new_price = float(current_price) + float(task.bid_step)
        if new_price <= float(task.max_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. Ставка повышена до {new_price} ₽.", level='WARNING')
            else:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. НЕ УДАЛОСЬ повысить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута макс. ставка {task.max_price} ₽.", level='WARNING')

    # Если мы ВЫШЕ нашего диапазона (например, на 1-м месте, а цель 5-10)
    elif position < task.target_position_min:
        new_price = float(current_price) - float(task.bid_step)
        if new_price >= float(task.min_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Экономия: позиция {position} < {task.target_position_min}. Ставка понижена до {new_price} ₽.", level='INFO')
            else:
                TaskLog.objects.create(task=task, message=f"Экономия: НЕ УДАЛОСЬ понизить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута мин. ставка {task.min_price} ₽.", level='INFO')
    
    # Если мы ВНУТРИ диапазона
    else:
        TaskLog.objects.create(task=task, message="Позиция в целевом диапазоне. Ставка не изменена.")

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

