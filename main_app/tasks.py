# main_app/tasks.py

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
    get_item_info,
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# =============================================================
# ПАРСИНГ ПОЗИЦИИ
# =============================================================

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Возвращает:
      {"position": N}   — нашли
      {"blocked": True} — 429/403, прокси заблокирован
      None              — реально не в топ-50
    """
    headers_list = [
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        },
        {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        },
    ]

    max_retries = 2  # Уменьшили с 5 до 2
    last_port = None

    for attempt in range(max_retries):
        proxies, proxy_used = get_random_proxy(exclude_port=last_port)
        last_port = proxy_used['port']
        headers = headers_list[attempt % len(headers_list)]

        try:
            pause = random.uniform(3, 7)
            logger.info(f"[PARSER] Попытка {attempt+1}/{max_retries} порт {proxy_used['port']} (пауза {pause:.1f}с)")
            time.sleep(pause)

            response = requests.get(
                search_url, headers=headers, proxies=proxies, timeout=30
            )

            if response.status_code in (429, 403):
                logger.warning(f"[PARSER] {response.status_code} порт {proxy_used['port']} — меняем IP")
                rotate_proxy_ip(proxy_used)
                time.sleep(10)  # Ждём после смены IP
                # Возвращаем blocked — не повышаем цену вслепую
                return {"blocked": True}

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            all_ads = soup.find_all('div', {'data-marker': 'item'})
            logger.info(f"[PARSER] Найдено {len(all_ads)} объявлений")

            if not all_ads:
                logger.warning("[PARSER] 0 объявлений — блок или пустая выдача")
                rotate_proxy_ip(proxy_used)
                return {"blocked": True}

            for index, ad_element in enumerate(all_ads):
                if ad_element.get('data-item-id') == str(ad_id):
                    position = index + 1
                    logger.info(f"[PARSER] ✅ {ad_id} на позиции {position}")
                    return {"position": position}

            logger.warning(f"[PARSER] {ad_id} не найден в {len(all_ads)} объявлениях")
            return None  # Реально не в топ-50

        except requests.exceptions.RequestException as e:
            logger.error(f"[PARSER] Ошибка попытки {attempt+1}: {e}")
            rotate_proxy_ip(proxy_used)

    logger.error(f"[PARSER] Все {max_retries} попытки провалились")
    return {"blocked": True}


# =============================================================
# ПРОВЕРКА РАСПИСАНИЯ
# =============================================================

def is_time_in_schedule(schedule_data) -> bool:
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
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    # --- Защита от частых запусков ---
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 120:
        logger.info(f"Задача {task_id} слишком частая — пропуск")
        if task.is_active:
            delay = 180 + random.randint(-30, 60)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- 1. Токен ---
    if not task.avito_account:
        TaskLog.objects.create(task=task, message="Задача не привязана к аккаунту Avito.", level='ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(args=[task_id], countdown=300 + random.randint(-60, 60))
        return

    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not access_token:
        TaskLog.objects.create(task=task, message="Не удалось получить токен.", level='ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(args=[task_id], countdown=300 + random.randint(-60, 60))
        return

    # --- 2. Расписание ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} вне расписания.")
        current_price = get_current_ad_price(task.ad_id, access_token)
        min_price = float(task.min_price)
        if current_price is not None and float(current_price) > min_price:
            if set_ad_price(task.ad_id, min_price, access_token, daily_limit_rub=float(task.daily_budget)):
                TaskLog.objects.create(task=task, message=f"↓ Снижена до {min_price} ₽ (вне расписания).", level='INFO')
                task.current_price = min_price
                task.save(update_fields=['current_price'])
        if task.is_active:
            run_bidding_for_task.apply_async(args=[task_id], countdown=300 + random.randint(-60, 60))
        return

    # --- 3. Основная логика ---
    TaskLog.objects.create(task=task, message=f"▶ Биддер для {task.ad_id}")

    ad_data = get_ad_position(task.search_url, task.ad_id)

    # --- Заблокировано (429/403) — пропускаем, не меняем цену ---
    if ad_data is not None and ad_data.get("blocked"):
        TaskLog.objects.create(
            task=task,
            message="⚠️ Авито заблокировал запрос (429/403) — цена не изменена, пропуск.",
            level='WARNING'
        )
        task.save(update_fields=['current_position'])
        if task.is_active:
            # Ждём дольше после блокировки
            delay = 600 + random.randint(-60, 120)
            logger.info(f"Задача {task_id} заблокирована → ждём {delay} сек")
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
        return

    # --- Не найдено в топ-50 (реально) ---
    if ad_data is None:
        TaskLog.objects.create(
            task=task,
            message="Объявление не найдено в топ-50 — цена не изменена.",
            level='WARNING'
        )
        task.current_position = None
        task.save(update_fields=['current_position'])

    # --- Найдено ---
    else:
        position = ad_data["position"]
        current_price = get_current_ad_price(task.ad_id, access_token)

        task.current_position = position
        if current_price is not None:
            task.current_price = current_price
        task.save(update_fields=['current_position', 'current_price'])

        TaskLog.objects.create(
            task=task,
            message=f"📍 Позиция: {position} (цель {task.target_position_min}–{task.target_position_max}), ставка: {current_price or '—'} ₽"
        )

        if current_price is None:
            TaskLog.objects.create(task=task, message="Не удалось получить цену.", level='ERROR')
        elif position > task.target_position_max:
            new_price = float(current_price) + float(task.bid_step)
            if new_price <= float(task.max_price):
                if set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(
                        task=task,
                        message=f"↑ Повышена до {new_price} ₽ (позиция {position} > {task.target_position_max})",
                        level='WARNING'
                    )
                else:
                    TaskLog.objects.create(task=task, message="Ошибка повышения", level='ERROR')
            else:
                TaskLog.objects.create(task=task, message=f"Достигнут максимум {task.max_price} ₽", level='WARNING')
        else:
            new_price = float(current_price) - float(task.bid_step)
            if new_price >= float(task.min_price):
                if set_ad_price(task.ad_id, new_price, access_token, daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(
                        task=task,
                        message=f"↓ Понижена до {new_price} ₽ (экономия, позиция {position} в норме)",
                        level='INFO'
                    )
                else:
                    TaskLog.objects.create(task=task, message="Ошибка понижения", level='ERROR')
            else:
                TaskLog.objects.create(task=task, message=f"Минимум {task.min_price} ₽ — не меняем", level='INFO')

    # --- Перепланирование ---
    TaskLog.objects.create(task=task, message="Цикл завершён ✔")
    if task.is_active:
        # Уникальный сдвиг по task_id чтобы задачи не запускались одновременно
        unique_offset = (task_id % 50) * 8  # разброс 0-400 сек
        delay = 400 + unique_offset + random.randint(-30, 30)
        logger.info(f"Задача {task_id} → через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


# =============================================================
# ОБНОВЛЕНИЕ TITLE + IMAGE
# =============================================================

@shared_task
def update_task_details(task_id: int):
    try:
        task = BiddingTask.objects.select_related('avito_account').get(pk=task_id)
    except BiddingTask.DoesNotExist:
        logger.error(f"[update_task_details] Задача {task_id} не найдена")
        return

    account = task.avito_account
    if not account:
        logger.error(f"[update_task_details] У задачи {task_id} нет аккаунта")
        return

    token = get_avito_access_token(account.avito_client_id, account.avito_client_secret)
    if not token:
        logger.error(f"[update_task_details] Нет токена")
        return

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
            logger.info(f"[update_task_details] ✅ {task_id}: «{task.title}»")
    else:
        logger.warning(f"[update_task_details] ❌ Нет данных для {task.ad_id}")