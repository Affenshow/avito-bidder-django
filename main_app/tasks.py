import logging
import platform
import time
import random
import json
import undetected_chromedriver as uc
import requests
from django.utils import timezone
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

@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def run_bidding_for_task(self, task_id: int):
    """
    Биддер с самопланированием — каждое объявление в своём времени.
    """
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    # Защита от слишком частых запусков (не чаще 3 минут)
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 180:
        logger.info(f"Задача {task_id} слишком частая — пропуск")
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} не по расписанию — пропуск")
    else:
        TaskLog.objects.create(task=task, message=f"Запуск биддера для {task.ad_id}")

        # Смена IP перед парсингом
        rotate_proxy_ip()
        time.sleep(random.uniform(10, 25))

        profile = task.user.profile
        if not profile.avito_client_id or not profile.avito_client_secret:
            TaskLog.objects.create(task=task, message="API-ключи не настроены.", level='ERROR')
        else:
            access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
            if not access_token:
                TaskLog.objects.create(task=task, message="Не удалось получить токен.", level='ERROR')
            else:
                ad_data = get_ad_position(task.search_url, task.ad_id)
                if ad_data is None:
                    TaskLog.objects.create(task=task, message="Ошибка парсера позиции.", level='ERROR')
                else:
                    position = ad_data.get("position")
                    current_price = get_current_ad_price(task.ad_id, access_token)

                    # Сохраняем для интерфейса
                    task.current_position = position
                    if current_price is not None:
                        task.current_price = current_price
                    task.save(update_fields=['current_position', 'current_price'])

                    TaskLog.objects.create(
                        task=task,
                        message=f"Позиция: {position} (цель {task.target_position_min}–{task.target_position_max}), ставка: {current_price or '—'} ₽"
                    )

                    if current_price is None:
                        TaskLog.objects.create(task=task, message="Не удалось получить цену.", level='ERROR')
                    else:
                                                            # Умная логика ставки с экономией при хорошей позиции
                     range_size = task.target_position_max - task.target_position_min + 1
                    good_position_threshold = task.target_position_min + (range_size // 2)  # верхняя половина диапазона — понижаем

                    if position > task.target_position_max:
                        # Плохо — повышаем
                        new_price = float(current_price) + float(task.bid_step)
                        if new_price <= float(task.max_price):
                            success = set_ad_price(task.ad_id, new_price, access_token)
                            if success:
                                TaskLog.objects.create(task=task, message=f"↑ Повышена до {new_price} ₽ (позиция {position} > {task.target_position_max})", level='WARNING')
                            else:
                                TaskLog.objects.create(task=task, message="Ошибка повышения ставки", level='ERROR')
                        else:
                            TaskLog.objects.create(task=task, message=f"Достигнут максимум {task.max_price} ₽", level='WARNING')

                    elif position <= good_position_threshold:
                        # Хорошая позиция (верхняя половина) — понижаем для экономии
                        new_price = float(current_price) - float(task.bid_step)
                        if new_price >= float(task.min_price):
                            success = set_ad_price(task.ad_id, new_price, access_token)
                            if success:
                                TaskLog.objects.create(task=task, message=f"↓ Понижена до {new_price} ₽ (экономия, позиция {position} хорошая)", level='INFO')
                            else:
                                TaskLog.objects.create(task=task, message="Ошибка понижения ставки", level='ERROR')
                        else:
                            TaskLog.objects.create(task=task, message=f"Достигнут минимум {task.min_price} ₽", level='INFO')
                    else:
                        TaskLog.objects.create(task=task, message="Позиция в норме — ставка не менялась")

        TaskLog.objects.create(task=task, message="Цикл завершён")

    # Самопланирование — каждые 5–7 минут рандомно
    if task.is_active:
        delay = 300 + random.randint(-120, 120)
        logger.info(f"Задача {task_id} перезапустится через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


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