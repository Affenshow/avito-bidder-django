# main_app/tasks.py

import logging
import time
import random
import json
from django.utils import timezone
from datetime import datetime
from celery import shared_task

from .avito_api import (
    get_avito_access_token,
    get_bids_table,
    find_bid_for_position,
    get_current_position_from_bids,
    set_ad_price,
    get_item_info,
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# =============================================================
# РАСПИСАНИЕ
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
            delay = 180 + random.randint(-30, 60)
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
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

    # --- Старт ---
    log(task, f"▶ Биддер для объявления {task.ad_id}")

    # --- Случайная задержка чтобы не стрелять одновременно ---
    jitter = random.uniform(0.5, 3.0)
    time.sleep(jitter)

    # --- Получаем таблицу ставок от Avito API ---
    bids_data = None
    for attempt in range(3):
        bids_data = get_bids_table(task.ad_id, access_token)
        if bids_data:
            break
        wait = 10 * (attempt + 1) + random.uniform(1, 5)
        logger.warning(f"[TASK {task_id}] getBids попытка {attempt+1}/3, ждём {wait:.1f}с")
        time.sleep(wait)

    if not bids_data:
        log(task, "Не удалось получить данные о ставках от Avito (429 или ошибка).", 'ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    bids = bids_data['bids']
    current_bid = bids_data['current_bid']
    min_price = float(task.min_price)
    max_price = float(task.max_price)
    target_min = task.target_position_min
    target_max = task.target_position_max

    # --- Определяем текущую позицию по таблице ставок ---
    current_position = get_current_position_from_bids(bids, current_bid)

    # Сохраняем в БД
    task.current_position = current_position
    if current_bid is not None:
        task.current_price = current_bid
    task.save(update_fields=['current_position', 'current_price'])

    log(
        task,
        f"📍 Позиция: {current_position or '?'} | "
        f"Ставка: {current_bid or '?'} ₽ | "
        f"Цель: {target_min}–{target_max} | "
        f"Лимиты: {min_price}–{max_price} ₽"
    )

    # --- Находим оптимальную ставку для целевой позиции ---
    needed_bid = find_bid_for_position(bids, target_min)

    if needed_bid is None:
        log(task, "Не удалось определить оптимальную ставку.", 'ERROR')
        if task.is_active:
            run_bidding_for_task.apply_async(
                args=[task_id], countdown=300 + random.randint(-60, 60)
            )
        return

    # Ограничиваем диапазоном пользователя
    optimal_price = max(min_price, min(needed_bid, max_price))

    log(
        task,
        f"💡 Оптимальная ставка для топ-{target_min}: "
        f"{needed_bid} ₽ → с учётом лимитов: {optimal_price} ₽"
    )

    # --- Принимаем решение ---
    current_bid_float = float(current_bid) if current_bid is not None else None

    if current_bid_float is not None and abs(current_bid_float - optimal_price) < 0.5:
        # Ставка уже оптимальна — не трогаем
        log(task, f"✅ Ставка {current_bid_float} ₽ уже оптимальна, изменений нет.")

    else:
        # Меняем ставку (в любую сторону — вверх или вниз)
        direction = "↑" if (current_bid_float is None or optimal_price > current_bid_float) else "↓"

        if set_ad_price(
            task.ad_id,
            optimal_price,
            access_token,
            daily_limit_rub=float(task.daily_budget) if task.daily_budget else None
        ):
            log(
                task,
                f"{direction} Ставка: {current_bid_float or '?'} ₽ → {optimal_price} ₽ "
                f"(позиция {current_position or '?'}, цель топ-{target_min}–{target_max})",
                'WARNING' if direction == '↑' else 'INFO'
            )
            task.current_price = optimal_price
            task.save(update_fields=['current_price'])
        else:
            log(task, f"❌ Ошибка установки ставки {optimal_price} ₽", 'ERROR')

    # --- Перепланирование ---
    log(task, "Цикл завершён ✔")
    if task.is_active:
        delay = 290 + random.randint(-60, 60)
        logger.info(f"Задача {task_id} → следующий запуск через {delay} сек")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)


# =============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================

def _handle_out_of_schedule(task, token):
    """Вне расписания — снижаем ставку до минимума."""
    min_price = float(task.min_price)
    current = task.current_price

    if current is not None and float(current) > min_price:
        if set_ad_price(
            task.ad_id,
            min_price,
            token,
            daily_limit_rub=float(task.daily_budget) if task.daily_budget else None
        ):
            log(task, f"↓ Снижена до {min_price} ₽ (вне расписания).", 'INFO')
            task.current_price = min_price
            task.save(update_fields=['current_price'])
    else:
        log(task, "Вне расписания, ставка уже на минимуме.", 'INFO')


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