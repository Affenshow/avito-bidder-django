# main_app/tasks.py

import logging
import time
import random
import json
import requests
from django.utils import timezone
from bs4 import BeautifulSoup
from typing import Optional
from datetime import datetime
from celery import shared_task

from .avito_api import (
    PROXY_POOL,
    get_avito_access_token,
    get_bids_table,
    set_ad_price,
    rotate_proxy_ip,
    get_random_proxy,
    get_item_info,
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# =============================================================
# ПАРСИНГ ПОЗИЦИИ — только позиция, всё остальное через API
# Было: 5 попыток, паузы 30-240 сек, возвращал dict {"position": N}
# Стало: 3 попытки, паузы 15-30 сек, возвращает int или None
# =============================================================

def get_ad_position(search_url: str, ad_id: int) -> Optional[int]:
    """
    Единственная задача — найти позицию объявления в поиске.
    Текущую ставку берём из API (быстрее и точнее).
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
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
        },
    ]

    # Было: 5 попыток, backoff 30/60/120/240 сек
    # Стало: 3 попытки, backoff 15/25 сек — быстрее, меньше нагрузка
    last_port = None

    for attempt in range(3):
        proxies, proxy_used = get_random_proxy(exclude_port=last_port)
        last_port = proxy_used['port']
        headers = headers_list[attempt % len(headers_list)]

        try:
            pause = random.uniform(2, 5)
            logger.info(
                f"[PARSER] Попытка {attempt+1}/3 "
                f"порт {proxy_used['port']} (пауза {pause:.1f}с)"
            )
            time.sleep(pause)

            response = requests.get(
                search_url, headers=headers, proxies=proxies, timeout=20
            )

            if response.status_code in (429, 403):
                rotate_proxy_ip(proxy_used)
                # Было: 30-240 сек. Стало: 15-30 сек
                wait = 15 + random.randint(0, 15)
                logger.warning(
                    f"[PARSER] {response.status_code} порт {proxy_used['port']} "
                    f"— смена IP, ждём {wait}с"
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            all_ads = soup.find_all('div', {'data-marker': 'item'})
            logger.info(f"[PARSER] Найдено {len(all_ads)} объявлений")

            if not all_ads:
                logger.warning("[PARSER] 0 объявлений — блок или пустая выдача")
                rotate_proxy_ip(proxy_used)
                time.sleep(15)
                continue

            for index, ad_element in enumerate(all_ads):
                if ad_element.get('data-item-id') == str(ad_id):
                    position = index + 1
                    logger.info(f"[PARSER] ✅ {ad_id} на позиции {position}")
                    return position  # Было: {"position": position}. Стало: int

            logger.warning(
                f"[PARSER] {ad_id} не найден "
                f"среди {len(all_ads)} объявлений — не в топе"
            )
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"[PARSER] Ошибка попытки {attempt+1}: {e}")
            rotate_proxy_ip(proxy_used)
            time.sleep(10)

    logger.error("[PARSER] Все 3 попытки провалились")
    return None


# =============================================================
# РАСПИСАНИЕ — не изменилось
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
        days = interval.get('days')
        if days and current_day_of_week not in days:
            continue
        try:
            start_str = interval.get('startTime') or interval.get('start')
            end_str = interval.get('endTime') or interval.get('end')
            if not start_str or not end_str:
                continue
            start_time = datetime.strptime(start_str, '%H:%M').time()
            end_time = datetime.strptime(end_str, '%H:%M').time()
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
# ЛОГИРОВАНИЕ
# =============================================================

def log(task, message, level='INFO'):
    TaskLog.objects.create(task=task, message=message, level=level)
    logger.info(f"[TASK {task.id}] {message}")


# =============================================================
# ОСНОВНОЙ БИДДЕР
# Было: прокси → позиция → API для ставки → решение
# Стало: API → ставка+recBid → прокси → позиция → решение
# =============================================================

@shared_task(bind=True, max_retries=5, default_retry_delay=300)
def run_bidding_for_task(self, task_id: int):

    # --- Загрузка задачи ---
    try:
        task = BiddingTask.objects.select_related('avito_account').get(
            id=task_id, is_active=True
        )
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    # --- Защита от частых запусков ---
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 120:
        logger.info(f"Задача {task_id} слишком частая — пропуск")
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=180 + random.randint(-30, 60)
            )
        return

    # --- Проверка аккаунта ---
    if not task.avito_account:
        log(task, "Задача не привязана к аккаунту Avito.", 'ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    # --- Токен ---
    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not access_token:
        log(task, "Не удалось получить токен.", 'ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    # --- Расписание ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} вне расписания.")
        _handle_out_of_schedule(task, access_token)
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    log(task, f"▶ Биддер для объявления {task.ad_id}")

    # ==========================================================
    # ШАГ 1 — API: текущая ставка и recBid
    # Было: get_current_ad_price (только цена)
    # Стало: get_bids_table (цена + recBid + таблица)
    # ==========================================================
    bids_data = None
    for attempt in range(3):
        bids_data = get_bids_table(task.ad_id, access_token)
        if bids_data:
            break
        wait = 10 * (attempt + 1) + random.uniform(1, 5)
        logger.warning(f"[TASK {task_id}] API попытка {attempt+1}/3, ждём {wait:.1f}с")
        time.sleep(wait)

    if not bids_data:
        log(task, "Не удалось получить данные от Avito API.", 'ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    current_bid = float(bids_data['current_bid']) if bids_data.get('current_bid') else None
    rec_bid = float(bids_data['rec_bid']) if bids_data.get('rec_bid') else None
    min_price = float(task.min_price)
    max_price = float(task.max_price)
    target_min = task.target_position_min
    target_max = task.target_position_max
    bid_step = float(task.bid_step)
    daily_budget = float(task.daily_budget) if task.daily_budget else None

    # Сохраняем текущую ставку из API
    if current_bid is not None:
        task.current_price = current_bid
        task.save(update_fields=['current_price'])

    log(
        task,
        f"📊 API: ставка={current_bid or '?'} ₽ | "
        f"рек.Avito={rec_bid or '?'} ₽ | "
        f"лимиты={min_price}–{max_price} ₽"
    )

    # ==========================================================
    # ШАГ 2 — ПРОКСИ: реальная позиция в поиске
    # Было: возвращал dict {"position": N}
    # Стало: возвращает int или None — чище и быстрее
    # ==========================================================
    position = get_ad_position(task.search_url, task.ad_id)

    if position is not None:
        task.current_position = position
        task.save(update_fields=['current_position'])
        log(
            task,
            f"📍 Позиция: {position} | "
            f"Цель: {target_min}–{target_max} | "
            f"Ставка: {current_bid or '?'} ₽"
        )
    else:
        task.current_position = None
        task.save(update_fields=['current_position'])
        log(task, "⚠️ Позиция не определена (не в топе или блок)", 'WARNING')

    # ==========================================================
    # ШАГ 3 — РЕШЕНИЕ по позиции
    # Логика та же что была, но ставку берём из API (точнее)
    # ==========================================================

    if position is None:
        # Не найдено — повышаем на шаг
        # Было: брали current_price из БД (могла быть устаревшей)
        # Стало: берём current_bid из API (актуальная)
        if task.freeze_price_if_not_found:
            log(task, "Цена заморожена (настройка freeze).", 'WARNING')
        else:
            base = current_bid if current_bid is not None else min_price
            new_price = min(base + bid_step, max_price)

            if current_bid is not None and float(current_bid) >= max_price:
                log(task, f"Достигнут максимум {max_price} ₽", 'WARNING')
            else:
                if set_ad_price(task.ad_id, new_price, access_token,
                                daily_limit_rub=daily_budget):
                    log(
                        task,
                        f"↑ Не в топе → {current_bid or '?'} ₽ → {new_price} ₽",
                        'WARNING'
                    )
                    task.current_price = new_price
                    task.save(update_fields=['current_price'])
                else:
                    log(task, f"❌ Ошибка установки {new_price} ₽", 'ERROR')

    elif position > target_max:
        # Ниже цели — ПОВЫШАЕМ на шаг
        base = current_bid if current_bid is not None else min_price
        new_price = min(base + bid_step, max_price)

        if current_bid is not None and float(current_bid) >= max_price:
            log(task, f"Достигнут максимум {max_price} ₽", 'WARNING')
        else:
            if set_ad_price(task.ad_id, new_price, access_token,
                            daily_limit_rub=daily_budget):
                log(
                    task,
                    f"↑ Позиция {position} > {target_max} → "
                    f"{current_bid or '?'} ₽ → {new_price} ₽",
                    'WARNING'
                )
                task.current_price = new_price
                task.save(update_fields=['current_price'])
            else:
                log(task, f"❌ Ошибка повышения до {new_price} ₽", 'ERROR')

    elif position < target_min:
        # Выше цели — ПОНИЖАЕМ на шаг (экономим)
        base = current_bid if current_bid is not None else max_price
        new_price = max(base - bid_step, min_price)

        if current_bid is not None and float(current_bid) <= min_price:
            log(task, f"Достигнут минимум {min_price} ₽", 'INFO')
        else:
            if set_ad_price(task.ad_id, new_price, access_token,
                            daily_limit_rub=daily_budget):
                log(
                    task,
                    f"↓ Позиция {position} < {target_min} → "
                    f"{current_bid or '?'} ₽ → {new_price} ₽ (экономия)",
                    'INFO'
                )
                task.current_price = new_price
                task.save(update_fields=['current_price'])
            else:
                log(task, f"❌ Ошибка снижения до {new_price} ₽", 'ERROR')

    else:
        # Позиция в цели — не трогаем
        log(
            task,
            f"✅ Позиция {position} в цели ({target_min}–{target_max}), "
            f"ставка {current_bid} ₽ не меняется."
        )

    log(task, "Цикл завершён ✔")
    if task.is_active:
        delay = 290 + random.randint(-60, 60)
        logger.info(f"Задача {task_id} → следующий запуск через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


# =============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================

def _handle_out_of_schedule(task, token):
    """Вне расписания — снижаем до минимума."""
    min_price = float(task.min_price)
    current = task.current_price

    if current is not None and float(current) > min_price:
        if set_ad_price(
            task.ad_id, min_price, token,
            daily_limit_rub=float(task.daily_budget) if task.daily_budget else None
        ):
            log(task, f"↓ Снижена до {min_price} ₽ (вне расписания).", 'INFO')
            task.current_price = min_price
            task.save(update_fields=['current_price'])
    else:
        log(task, "Вне расписания, ставка уже на минимуме.", 'INFO')


# =============================================================
# ОБНОВЛЕНИЕ TITLE + IMAGE — не изменилось
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

    token = get_avito_access_token(
        account.avito_client_id,
        account.avito_client_secret
    )
    if not token:
        logger.error(f"[update_task_details] Нет токена")
        return

    info = get_item_info(token, task.ad_id)

    if info:
        updated_fields = []
        if info.get('title'):
            task.title = info['title']
            updated_fields.append('title')
        if info.get('image_url'):
            task.image_url = info['image_url']
            updated_fields.append('image_url')
        if updated_fields:
            task.save(update_fields=updated_fields)
            logger.info(f"[update_task_details] ✅ {task_id}: «{task.title}»")
    else:
        logger.warning(f"[update_task_details] ❌ Нет данных для {task.ad_id}")