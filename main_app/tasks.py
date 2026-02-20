import logging
import time
import random
import json
import requests
from django.utils import timezone
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
from celery import shared_task

from .avito_api import (
    PROXY_POOL,
    get_avito_access_token,
    get_current_ad_price,
    set_ad_price,
    rotate_proxy_ip,
    get_random_proxy,
    get_item_info,  # ← НОВЫЙ ИМПОРТ
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# =============================================================
# ПАРСИНГ ПОЗИЦИИ (остаётся — нужен для определения позиции)
# =============================================================

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Парсит поисковую выдачу Avito и возвращает позицию объявления.
    НЕ используется для получения title/image — для этого есть API.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    max_retries = 3
    proxy_used = None

    for attempt in range(max_retries):
        proxies, proxy_used = get_random_proxy()
        try:
            logger.info(f"[REQUESTS] Попытка {attempt+1}/{max_retries} через прокси порт {proxy_used['port']}")
            response = requests.get(search_url, headers=headers, proxies=proxies, timeout=40)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            all_ads = soup.find_all('div', {'data-marker': 'item'})
            logger.info(f"[REQUESTS] Найдено {len(all_ads)} объявлений.")
            if not all_ads:
                return None

            for index, ad_element in enumerate(all_ads):
                if ad_element.get('data-item-id') == str(ad_id):
                    position = index + 1
                    logger.info(f"[REQUESTS] Объявление {ad_id} на позиции {position}")
                    # Возвращаем ТОЛЬКО позицию — title и image берём из API
                    return {"position": position}

            logger.warning(f"[REQUESTS] Объявление {ad_id} не найдено в выдаче")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[REQUESTS] Ошибка на попытке {attempt+1} "
                f"(прокси {proxy_used['port'] if proxy_used else '?'}): {e}"
            )
            if proxy_used is not None:
                rotate_proxy_ip(proxy_used)
            time.sleep(8 + random.uniform(0, 8))

    logger.error(f"[REQUESTS] Все {max_retries} попытки провалились")
    return None


# =============================================================
# ПРОВЕРКА РАСПИСАНИЯ
# =============================================================

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


# =============================================================
# ОСНОВНОЙ БИДДЕР
# =============================================================

@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def run_bidding_for_task(self, task_id: int):
    """
    Биддер с самопланированием, поддержкой "Выхода из 50-го места"
    и управлением дневным бюджетом.
    """
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    # --- Защита от частых запусков ---
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 180:
        logger.info(f"Задача {task_id} слишком частая — пропуск")
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 1. Получение токена ---
    if not task.avito_account:
        TaskLog.objects.create(
            task=task,
            message="Задача не привязана к аккаунту Avito. Работа невозможна.",
            level='ERROR'
        )
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not access_token:
        TaskLog.objects.create(
            task=task,
            message="Не удалось получить токен от аккаунта Avito.",
            level='ERROR'
        )
        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 2. Проверка расписания ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} не по расписанию. Снижаем цену до минимума.")
        current_price = get_current_ad_price(task.ad_id, access_token)
        min_price = float(task.min_price)
        if current_price is not None and float(current_price) > min_price:
            if set_ad_price(task.ad_id, min_price, access_token, daily_limit_rub=float(task.daily_budget)):
                TaskLog.objects.create(
                    task=task,
                    message=f"↓ Цена снижена до минимума {min_price} ₽ (вне расписания).",
                    level='INFO'
                )
                task.current_price = min_price
                task.save(update_fields=['current_price'])

        if task.is_active:
            delay = 300 + random.randint(-120, 120)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 3. Основная логика биддера ---
    TaskLog.objects.create(task=task, message=f"Запуск биддера для {task.ad_id}")
    proxies, proxy_used = get_random_proxy()
    rotate_proxy_ip(proxy_used)
    time.sleep(random.uniform(5, 15))

    ad_data = get_ad_position(task.search_url, task.ad_id)

    # --- Объявление НЕ найдено в топ-50 ---
    if ad_data is None:
        TaskLog.objects.create(
            task=task,
            message="Ошибка парсера: объявление не найдено в топ-50.",
            level='ERROR'
        )
        task.current_position = None

        if task.freeze_price_if_not_found:
            TaskLog.objects.create(
                task=task,
                message="Цена заморожена (согласно настройке).",
                level='WARNING'
            )
        else:
            current_price_from_db = task.current_price
            if current_price_from_db is None:
                new_price = float(task.min_price)
                log_message = f"↑ (Первый толчок) Установлена минимальная цена {new_price} ₽."
            else:
                new_price = float(current_price_from_db) + float(task.bid_step)
                log_message = f"↑ (Вслепую) Повышена до {new_price} ₽."

            if new_price <= float(task.max_price):
                if set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(task=task, message=log_message, level='WARNING')
                    task.current_price = new_price
                else:
                    TaskLog.objects.create(
                        task=task,
                        message=f"Ошибка установки цены {new_price} ₽.",
                        level='ERROR'
                    )
            else:
                TaskLog.objects.create(
                    task=task,
                    message=f"Достигнут максимум {task.max_price} ₽.",
                    level='WARNING'
                )

        task.save(update_fields=['current_position', 'current_price'])

    # --- Объявление НАЙДЕНО ---
    else:
        position = ad_data.get("position")
        current_price = get_current_ad_price(task.ad_id, access_token)

        task.current_position = position
        if current_price is not None:
            task.current_price = current_price
        task.save(update_fields=['current_position', 'current_price'])

        TaskLog.objects.create(
            task=task,
            message=f"Позиция: {position} "
                    f"(цель {task.target_position_min}–{task.target_position_max}), "
                    f"ставка: {current_price or '—'} ₽"
        )

        if current_price is None:
            TaskLog.objects.create(
                task=task,
                message="Не удалось получить цену.",
                level='ERROR'
            )
        else:
            if position > task.target_position_max:
                new_price = float(current_price) + float(task.bid_step)
                if new_price <= float(task.max_price):
                    success = set_ad_price(
                        task.ad_id, new_price, access_token,
                        daily_limit_rub=float(task.daily_budget)
                    )
                    if success:
                        TaskLog.objects.create(
                            task=task,
                            message=f"↑ Повышена до {new_price} ₽ "
                                    f"(позиция {position} > {task.target_position_max})",
                            level='WARNING'
                        )
                    else:
                        TaskLog.objects.create(
                            task=task,
                            message="Ошибка повышения ставки",
                            level='ERROR'
                        )
                else:
                    TaskLog.objects.create(
                        task=task,
                        message=f"Достигнут максимум {task.max_price} ₽",
                        level='WARNING'
                    )
            else:
                new_price = float(current_price) - float(task.bid_step)
                if new_price >= float(task.min_price):
                    success = set_ad_price(
                        task.ad_id, new_price, access_token,
                        daily_limit_rub=float(task.daily_budget)
                    )
                    if success:
                        TaskLog.objects.create(
                            task=task,
                            message=f"↓ Понижена до {new_price} ₽ "
                                    f"(экономия, позиция {position} в норме)",
                            level='INFO'
                        )
                    else:
                        TaskLog.objects.create(
                            task=task,
                            message="Ошибка понижения ставки",
                            level='ERROR'
                        )
                else:
                    TaskLog.objects.create(
                        task=task,
                        message=f"Достигнут минимум {task.min_price} ₽ — ставка не меняется",
                        level='INFO'
                    )

    # --- 5. Перепланирование ---
    TaskLog.objects.create(task=task, message="Цикл завершён")
    if task.is_active:
        delay = 120 + random.randint(-60, 60)
        logger.info(f"Задача {task_id} перезапустится через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


# =============================================================
# ОБНОВЛЕНИЕ TITLE + IMAGE ЧЕРЕЗ API (НОВОЕ!)
# =============================================================

@shared_task
def update_task_details(task_id: int):
    """
    Обновляет title и image_url через Avito API (не парсинг!).
    Вызывается после создания/редактирования задачи.
    """
    try:
        task = BiddingTask.objects.select_related('avito_account').get(pk=task_id)
    except BiddingTask.DoesNotExist:
        logger.error(f"[update_task_details] Задача {task_id} не найдена")
        return

    account = task.avito_account
    if not account:
        logger.error(f"[update_task_details] У задачи {task_id} нет аккаунта")
        return

    # Получаем токен
    token = get_avito_access_token(
        account.avito_client_id,
        account.avito_client_secret
    )
    if not token:
        logger.error(f"[update_task_details] Нет токена для аккаунта «{account.name}»")
        return

    # Получаем данные через API
    info = get_item_info(token, task.ad_id)

    if info:
        updated_fields = []

        if info.get("title"):
            task.title = info["title"]
            updated_fields.append("title")

        if info.get("image_url"):
            task.image_url = info["image_url"]
            updated_fields.append("image_url")

        if updated_fields:
            task.save(update_fields=updated_fields)
            logger.info(
                f"[update_task_details] ✅ Задача {task_id}: "
                f"title=«{task.title}», image={'да' if info.get('image_url') else 'нет'}"
            )
        else:
            logger.warning(f"[update_task_details] API вернул пустые данные для {task.ad_id}")
    else:
        logger.warning(
            f"[update_task_details] ❌ Не удалось получить инфо для {task.ad_id}. "
            f"Объявление удалено или нет доступа."
        )