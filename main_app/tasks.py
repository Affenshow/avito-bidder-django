# main_app/tasks.py

import logging
import main_app.captcha_solver as cs
import time
import random
import json
import requests
from django.utils import timezone
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
from celery import shared_task
from .captcha_solver import get_avito_session

from .avito_api import (
    ROTATING_PROXY,
    get_avito_access_token,
    get_current_ad_price,
    set_ad_price,
    get_item_info,
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# =============================================================
# ПАРСИНГ ПОЗИЦИИ — ROTATING PROXY
# =============================================================

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    for attempt in range(3):
        try:
            pause = random.uniform(2, 4)
            logger.info(f"[PARSER] Попытка {attempt+1}/3 (пауза {pause:.1f}с)")
            time.sleep(pause)

            session = get_avito_session(proxies=ROTATING_PROXY)
            if not session:
                logger.error("[PARSER] Не удалось получить сессию")
                time.sleep(15)
                continue

            # Только текст — без картинок, css, js
            session.headers.update({
                'Accept': 'text/html',
                'Accept-Encoding': 'gzip, deflate',  # сжатие — меньше трафика
            })

            # Добавляем только нужную страницу и минимум параметров
            # Убираем лишние параметры из URL если есть
            clean_url = search_url.split('?')[0]  # без параметров если не нужны
            # Если параметры нужны — оставляем search_url как есть

            response = session.get(
                search_url,
                timeout=20,
                stream=False,  # не стримить
            )

            if response.status_code == 429:
              wait = 30 + random.randint(0, 15)
              logger.warning(f"[PARSER] 429 — сбрасываем сессию, ждём {wait}с")
              # Сбрасываем кеш — при следующей попытке создастся новая сессия
              cs._cached_session = None
              cs._session_created_at = 0
              time.sleep(wait)
              continue

            if response.status_code == 403:
                wait = 30 + random.randint(0, 15)
                logger.warning(f"[PARSER] 403 — ждём {wait}с")
                time.sleep(wait)
                continue

            if 'Доступ ограничен' in response.text or 'firewallCaptcha' in response.text:
               logger.warning("[PARSER] Капча — сбрасываем кеш сессии")
               # Сбрасываем кеш чтобы пересоздать сессию
               cs._cached_session = None
               cs._session_created_at = 0
               time.sleep(15)
               continue

            response.raise_for_status()

            # Ищем только data-item-id в тексте — без полного парсинга BeautifulSoup
            import re
            item_ids = re.findall(r'data-item-id=["\'](\d+)["\']', response.text)
            logger.info(f"[PARSER] Найдено {len(item_ids)} объявлений")

            if not item_ids:
                logger.warning("[PARSER] 0 объявлений — возможно блок")
                time.sleep(15)
                continue

            for index, found_id in enumerate(item_ids):
                if found_id == str(ad_id):
                    position = index + 1
                    logger.info(f"[PARSER] ✅ {ad_id} на позиции {position}")
                    return {"position": position}

            logger.warning(f"[PARSER] {ad_id} не найден среди {len(item_ids)}")
            return None  # нашли страницу, объявления есть — но нашего нет, не повторяем

        except requests.exceptions.ProxyError as e:
            logger.error(f"[PARSER] Ошибка прокси {attempt+1}: {e}")
            time.sleep(10)

        except requests.exceptions.Timeout:
            logger.error(f"[PARSER] Таймаут {attempt+1}")
            time.sleep(10)

        except Exception as e:
            logger.error(f"[PARSER] Ошибка {attempt+1}: {e}")
            time.sleep(10)

    logger.error("[PARSER] Все попытки провалились")
    return None


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
        TaskLog.objects.create(
            task=task,
            message="Задача не привязана к аккаунту Avito.",
            level='ERROR'
        )
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not access_token:
        TaskLog.objects.create(
            task=task,
            message="Не удалось получить токен.",
            level='ERROR'
        )
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    # --- 2. Расписание ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} вне расписания.")
        current_price = get_current_ad_price(task.ad_id, access_token)
        min_price = float(task.min_price)
        if current_price is not None and float(current_price) > min_price:
            if set_ad_price(task.ad_id, min_price, access_token,
                            daily_limit_rub=float(task.daily_budget)):
                TaskLog.objects.create(
                    task=task,
                    message=f"↓ Снижена до {min_price} ₽ (вне расписания).",
                    level='INFO'
                )
                task.current_price = min_price
                task.save(update_fields=['current_price'])

        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    # --- 3. Основная логика ---
    logger.info(f"[TASK {task_id}] ▶ Биддер для объявления {task.ad_id}")
    TaskLog.objects.create(task=task, message=f"▶ Биддер для {task.ad_id}")

    # Парсим позицию через rotating прокси
    ad_data = get_ad_position(task.search_url, task.ad_id)

    # --- Не найдено ---
    if ad_data is None:
        TaskLog.objects.create(
            task=task,
            message="⚠️ Позиция не определена (не в топе или блок).",
            level='ERROR'
        )
        task.current_position = None

        if task.freeze_price_if_not_found:
            TaskLog.objects.create(
                task=task,
                message="Цена заморожена (настройка).",
                level='WARNING'
            )
        else:
            current_price_from_db = task.current_price
            if current_price_from_db is None:
                new_price = float(task.min_price)
                log_msg = f"↑ Первый запуск: {new_price} ₽"
            else:
                new_price = float(current_price_from_db) + float(task.bid_step)
                log_msg = f"↑ Не в топе → {current_price_from_db} ₽ → {new_price} ₽"

            if new_price <= float(task.max_price):
                if set_ad_price(task.ad_id, new_price, access_token,
                                daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(
                        task=task, message=log_msg, level='WARNING'
                    )
                    task.current_price = new_price
                else:
                    TaskLog.objects.create(
                        task=task,
                        message=f"Ошибка установки {new_price} ₽",
                        level='ERROR'
                    )
            else:
                TaskLog.objects.create(
                    task=task,
                    message=f"Достигнут максимум {task.max_price} ₽",
                    level='WARNING'
                )

        task.save(update_fields=['current_position', 'current_price'])

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
            message=f"📍 Позиция: {position} "
                    f"(цель {task.target_position_min}–{task.target_position_max}), "
                    f"ставка: {current_price or '—'} ₽"
        )

        if current_price is None:
            TaskLog.objects.create(
                task=task, message="Не удалось получить цену.", level='ERROR'
            )
        elif position > task.target_position_max:
            new_price = float(current_price) + float(task.bid_step)
            if new_price <= float(task.max_price):
                if set_ad_price(task.ad_id, new_price, access_token,
                                daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(
                        task=task,
                        message=f"↑ Повышена до {new_price} ₽ "
                                f"(позиция {position} > {task.target_position_max})",
                        level='WARNING'
                    )
                else:
                    TaskLog.objects.create(
                        task=task, message="Ошибка повышения", level='ERROR'
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
                if set_ad_price(task.ad_id, new_price, access_token,
                                daily_limit_rub=float(task.daily_budget)):
                    TaskLog.objects.create(
                        task=task,
                        message=f"↓ Понижена до {new_price} ₽ "
                                f"(экономия, позиция {position} в норме)",
                        level='INFO'
                    )
                else:
                    TaskLog.objects.create(
                        task=task, message="Ошибка понижения", level='ERROR'
                    )
            else:
                TaskLog.objects.create(
                    task=task,
                    message=f"Минимум {task.min_price} ₽ — не меняем",
                    level='INFO'
                )

    # --- Перепланирование ---
    TaskLog.objects.create(task=task, message="Цикл завершён ✔")
    if task.is_active:
        delay = 290 + random.randint(-60, 60)
        logger.info(f"Задача {task_id} → следующий запуск через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


# =============================================================
# ОБНОВЛЕНИЕ TITLE + IMAGE
# =============================================================

@shared_task
def update_task_details(task_id: int):
    logger.info(f"[update_task_details] ▶ Начало для задачи {task_id}")
    try:
        task = BiddingTask.objects.select_related('avito_account').get(pk=task_id)
    except BiddingTask.DoesNotExist:
        logger.error(f"[update_task_details] Задача {task_id} не найдена")
        return

    account = task.avito_account
    if not account:
        logger.error(f"[update_task_details] У задачи {task_id} нет аккаунта")
        return

    logger.info(f"[update_task_details] Получение токена для {account.avito_client_id[:10]}...")
    token = get_avito_access_token(
        account.avito_client_id,
        account.avito_client_secret
    )
    if not token:
        logger.error(f"[update_task_details] Нет токена для задачи {task_id}")
        return

    logger.info(f"[update_task_details] Запрос данных для ad_id={task.ad_id}")
    info = get_item_info(token, task.ad_id)
    logger.info(f"[update_task_details] Ответ get_item_info: {info}")

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
            logger.warning(f"[update_task_details] ⚠️ Данные пришли но нечего обновлять: {info}")
    else:
        logger.warning(f"[update_task_details] ❌ Нет данных для ad_id={task.ad_id}")