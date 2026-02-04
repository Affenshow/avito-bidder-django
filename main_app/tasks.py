import logging
import platform
import time
import random
import json
import undetected_chromedriver as uc
import requests
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price, rotate_proxy_ip
from .models import BiddingTask, TaskLog


logger = logging.getLogger(__name__)

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Парсит страницу с помощью requests, используя прокси.
    Возвращает словарь с позицией, заголовком и URL картинки.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    # --- Ваши НОВЫЕ, правильные настройки прокси ---
    proxy_user = "uKuNaf"
    proxy_pass = "FAjEC5HeK7yt"
    proxy_host = "mproxy.site"
    proxy_port = 17563
    proxies = {
       'http': f'http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}',
       'https': f'http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}',
    }

    try:
        logger.info(f"--- [REQUESTS] Запрос к {search_url} через прокси...")
        
        # Делаем запрос с прокси и таймаутом
        response = requests.get(search_url, headers=headers, proxies=proxies, timeout=20)
        
        # Проверяем, что Avito не заблокировал нас
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Ищем все блоки объявлений
        all_ads = soup.find_all('div', {'data-marker': 'item'})
        logger.info(f"--- [REQUESTS] Найдено {len(all_ads)} объявлений на странице.")

        if not all_ads:
            return None

        for index, ad_element in enumerate(all_ads):
            # BeautifulSoup использует .get(), а не .get_attribute()
            if ad_element.get('data-item-id') == str(ad_id):
                position = index + 1
                title = "Название не найдено"
                image_url = None
                
                try:
                    title_tag = ad_element.find('a', {'data-marker': 'item-title'})
                    if title_tag:
                        title = title_tag.text.strip()
                except Exception:
                    logger.warning("Не удалось найти заголовок.")

                try:
                    # Ищем тег <img> внутри контейнера с картинкой
                    img_container = ad_element.find('div', class_=lambda x: x and 'photo-slider-item' in x)
                    if img_container:
                        img_tag = img_container.find('img')
                        if img_tag:
                            image_url = img_tag.get('src')
                except Exception:
                     logger.warning("Не удалось найти картинку.")
                
                logger.info(f"--- [REQUESTS] Найдено объявление {ad_id} на позиции {position}! ---")
                return {"position": position, "title": title, "image_url": image_url}
        
        logger.warning(f"--- [REQUESTS] Объявление {ad_id} НЕ найдено на странице.")
        return None
    
    except requests.exceptions.RequestException as e:
        logger.error(f"--- [REQUESTS] КРИТИЧЕСКАЯ ОШИБКА: {e}")
        return None

def is_time_in_schedule(schedule_data) -> bool:
    schedule = []
    if isinstance(schedule_data, str):
        try:
            schedule = json.loads(schedule_data)
        except json.JSONDecodeError:
            schedule = []
    elif isinstance(schedule_data, list):
        schedule = schedule_data
    if not schedule:
        return True
    now = datetime.now().time()
    for interval in schedule:
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

from celery import shared_task

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

    # --- 2. Проверяем расписание ---
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
        TaskLog.objects.create(task=task, message="Не удалось получить токен доступа.", level='ERROR')
        return

    # --- 4. МЕНЯЕМ IP-АДРЕС ПРОКСИ (НОВЫЙ ШАГ) ---
    ip_changed = rotate_proxy_ip()
    if ip_changed:
        TaskLog.objects.create(task=task, message="IP-адрес прокси успешно сменен.")
        time.sleep(10) # Даем 10 секунд на "прогрев" нового IP
    else:
        TaskLog.objects.create(task=task, message="Не удалось сменить IP-адрес прокси. Работаем со старым.", level='WARNING')


    # --- 5. Получаем актуальную информацию с Avito ---
    ad_data = get_ad_position(task.search_url, task.ad_id)
    if ad_data is None:
        TaskLog.objects.create(task=task, message="Не удалось получить информацию с Avito (ошибка парсера).", level='ERROR')
        return
    
    position = ad_data.get("position")
    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: [{task.target_position_min} - {task.target_position_max}].")

    # --- 6. Получаем реальную текущую цену через API ---
    current_price = get_current_ad_price(task.ad_id, access_token)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену через API.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

    # --- 7. "Умный" алгоритм биддера ---
    if position > task.target_position_max:
        # --- ЛОГИКА ПОВЫШЕНИЯ ---
        new_price = float(current_price) + float(task.bid_step)
        if new_price <= float(task.max_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. Ставка повышена до {new_price} ₽.", level='WARNING')
            else:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. НЕ УДАЛОСЬ повысить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута макс. ставка {task.max_price} ₽.", level='WARNING')

    elif position < task.target_position_min:
        # --- ЛОГИКА ПОНИЖЕНИЯ ---
        new_price = float(current_price) - float(task.bid_step)
        if new_price >= float(task.min_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Экономия: позиция {position} < {task.target_position_min}. Ставка понижена до {new_price} ₽.", level='INFO')
            else:
                TaskLog.objects.create(task=task, message=f"Экономия: НЕ УДАЛОСЬ понизить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута мин. ставка {task.min_price} ₽.", level='INFO')
    
    else:
        TaskLog.objects.create(task=task, message="Позиция в целевом диапазоне. Ставка не изменена.")

    TaskLog.objects.create(task=task, message="Биддер завершил работу.")

@shared_task
def trigger_all_active_tasks():
    logger.info(">>> ПЛАНИРОВЩИК: Поиск активных задач...")
    active_tasks = BiddingTask.objects.filter(is_active=True)
    for task in active_tasks:
        run_bidding_for_task.delay(task.id)
    logger.info(f">>> ПЛАНИРОВЩИК: Запущено {active_tasks.count()} задач.")

@shared_task
def update_task_details(task_id: int):
    """Фоновая задача для получения title и image_url."""
    try:
        task = BiddingTask.objects.get(pk=task_id)
        ad_data = get_ad_position(task.search_url, task.ad_id)
        if ad_data:
            task.title = ad_data.get('title', 'Название не найдено')
            task.image_url = ad_data.get('image_url')
            task.save(update_fields=['title', 'image_url'])
            logger.info(f"Обновлена информация для задачи #{task_id}")
    except BiddingTask.DoesNotExist:
        pass