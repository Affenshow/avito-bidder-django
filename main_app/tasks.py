import logging
import platform
import time
import random
import json
import requests
from django.utils import timezone
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
from celery import shared_task

# Ваши импорты
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
        response = requests.get(search_url, headers=headers, proxies=proxies, timeout=35)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        all_ads = soup.find_all('div', {'data-marker': 'item'})
        logger.info(f"--- [REQUESTS] Найдено {len(all_ads)} объявлений на странице.")
        if not all_ads:
            return None
        
        for index, ad_element in enumerate(all_ads):
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
    """
    Проверяет, соответствует ли текущее время (включая день недели)
    хотя бы одному из интервалов в расписании.
    """
    schedule_list = []
    if isinstance(schedule_data, str):
        try:
            schedule_list = json.loads(schedule_data)
        except json.JSONDecodeError:
            schedule_list = []
    elif isinstance(schedule_data, list):
        schedule_list = schedule_data
    
    if not schedule_list:
        return True

    now = datetime.now()
    current_day_of_week = now.weekday() + 1
    current_time = now.time()

    for interval in schedule_list:
        days = interval.get("days")
        if days:
            if current_day_of_week not in days:
                continue
        
        try:
            start_str = interval.get("startTime") or interval.get("start")
            end_str = interval.get("endTime") or interval.get("end")
            if not start_str or not end_str:
                continue
            
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            
            if start_time <= end_time:
                if start_time <= current_time < end_time:
                    return True
            else:
                if current_time >= start_time or current_time < end_time:
                    return True
        except (ValueError, TypeError):
            continue
            
    return False


@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def run_bidding_for_task(self, task_id: int):
    """
    Биддер с самопланированием, поддержкой "Выхода из 50-го места" и
    упрощенным управлением дневным бюджетом.
    """
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    # --- ВАША ЗАЩИТА ОТ ЧАСТЫХ ЗАПУСКОВ (без изменений) ---
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 180:
        logger.info(f"Задача {task_id} слишком частая — пропуск")
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 1. ПОЛУЧЕНИЕ ТОКЕНА (без изменений) ---
    if not task.avito_account:
        # ... (код обработки отсутствия аккаунта)
        return
    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not access_token:
        # ... (код обработки отсутствия токена)
        return

    # --- 2. ПРОВЕРКА РАСПИСАНИЯ (без изменений) ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} не по расписанию. Снижаем цену до минимума.")
        current_price = get_current_ad_price(task.ad_id, access_token)
        min_price = float(task.min_price)
        if current_price is not None and float(current_price) > min_price:
            # Ваша функция set_ad_price уже умеет работать без лимита
            if set_ad_price(task.ad_id, min_price, access_token):
                TaskLog.objects.create(task=task, message=f"↓ Цена снижена до минимума {min_price} ₽ (вне расписания).", level='INFO')
                task.current_price = min_price
                task.save(update_fields=['current_price'])
        
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 3. ОСНОВНАЯ ЛОГИКА БИДДЕРА ---
    TaskLog.objects.create(task=task, message=f"Запуск биддера для {task.ad_id}")
    rotate_proxy_ip()
    time.sleep(random.uniform(10, 25))
    
    ad_data = get_ad_position(task.search_url, task.ad_id)

    # --- Логика "Выхода из 50-го места" (без изменений) ---
    if ad_data is None:
        # ... (весь ваш код для ad_data is None, включая вызов set_ad_price с лимитом)
        # Убедитесь, что внутри этого блока вызов выглядит так:
        # set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget))
        pass # Этот код у вас уже есть
    else:
        # --- Логика для найденного объявления (с единственным изменением) ---
        position = ad_data.get("position")
        current_price = get_current_ad_price(task.ad_id, access_token)
        
        task.current_position = position
        if current_price is not None:
            task.current_price = current_price
        task.save(update_fields=['current_position', 'current_price'])
        
        TaskLog.objects.create(task=task, message=f"Позиция: {position} (цель {task.target_position_min}–{task.target_position_max}), ставка: {current_price or '—'} ₽")
        
        if current_price is None:
            TaskLog.objects.create(task=task, message="Не удалось получить цену.", level='ERROR')
        else:
            # +++ ИЗМЕНЕНИЕ: ПОЛНОСТЬЮ УДАЛЕН НЕПРАВИЛЬНЫЙ БЛОК ПРОВЕРКИ БЮДЖЕТА +++
            # Мы больше не "гадаем" о бюджете по позиции.
            
            # Ваша умная логика ставки (без изменений)
            if position > task.target_position_max:
                new_price = float(current_price) + float(task.bid_step)
                if new_price <= float(task.max_price):
                    # Просто передаем лимит в функцию. Avito сам разберется.
                    success = set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget))
                    if success:
                        TaskLog.objects.create(task=task, message=f"↑ Повышена до {new_price} ₽ (позиция {position} > {task.target_position_max})", level='WARNING')
                    else:
                        TaskLog.objects.create(task=task, message="Ошибка повышения ставки", level='ERROR')
                else:
                    TaskLog.objects.create(task=task, message=f"Достигнут максимум {task.max_price} ₽", level='WARNING')
            else:
                new_price = float(current_price) - float(task.bid_step)
                if new_price >= float(task.min_price):
                    # И здесь тоже просто передаем лимит.
                    success = set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget))
                    if success:
                        TaskLog.objects.create(task=task, message=f"↓ Понижена до {new_price} ₽ (экономия, позиция {position} в норме или лучше)", level='INFO')
                    else:
                        TaskLog.objects.create(task=task, message="Ошибка понижения ставки", level='ERROR')
                else:
                    TaskLog.objects.create(task=task, message=f"Достигнут минимум {task.min_price} ₽ — ставка не меняется", level='INFO')

    # --- 5. ФИНАЛЬНОЕ ПЕРЕПЛАНИРОВАНИЕ (без изменений) ---
    TaskLog.objects.create(task=task, message="Цикл завершён")
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