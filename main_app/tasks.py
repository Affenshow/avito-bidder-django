
# main_app/tasks.py

import logging
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
    current_day = now.weekday() + 1
    current_time = now.time()

    for interval in schedule_list:
        days = interval.get('days')
        if days and current_day not in days:
            continue
        try:
            start_str = interval.get('startTime') or interval.get('start')
            end_str = interval.get('endTime') or interval.get('end')
            if not start_str or not end_str:
                continue
            start = datetime.strptime(start_str, '%H:%M').time()
            end = datetime.strptime(end_str, '%H:%M').time()
            if start <= end:
                if start <= current_time < end:
                    return True
            else:
                if current_time >= start or current_time < end:
                    return True
        except (ValueError, TypeError):
            continue

    return False


def log(task, message, level='INFO'):
    TaskLog.objects.create(task=task, message=message, level=level)
    logger.info(f"[TASK {task.id}] {message}")


# =============================================================
# ОСНОВНОЙ БИДДЕР
# =============================================================
@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_bidding_for_task(self, task_id: int):
    # --- Загрузка задачи ---
    try:
        task = BiddingTask.objects.select_related('avito_account').get(
            id=task_id, is_active=True
        )
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} не найдена или отключена")
        return

    # --- Защита от частых запусков ---
    last_log = TaskLog.objects.filter(task=task).order_by('-timestamp').first()
    if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 120:
        logger.info(f"Задача {task_id} — слишком частый запуск, пропуск")
        _reschedule(task_id)
        return

    # --- Проверка аккаунта ---
    if not task.avito_account:
        log(task, "Задача не привязана к аккаунту Avito", 'ERROR')
        _reschedule(task_id)
        return

    # --- Токен ---
    token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not token:
        log(task, "Не удалось получить токен Avito", 'ERROR')
        _reschedule(task_id)
        return

    # --- Расписание ---
    if not is_time_in_schedule(task.schedule):
        _handle_out_of_schedule(task, token)
        _reschedule(task_id)
        return

    # --- Получаем таблицу ставок от Avito ---
    log(task, f"▶ Запуск биддера для объявления {task.ad_id}")

    bids_data = get_bids_table(task.ad_id, token)

    if not bids_data:
        log(task, "Не удалось получить данные о ставках от Avito", 'ERROR')
        _reschedule(task_id)
        return

    bids = bids_data['bids']
    current_bid = bids_data['current_bid']
    min_price = float(task.min_price)
    max_price = float(task.max_price)

    # --- Определяем текущую позицию ---
    current_position = get_current_position_from_bids(bids, current_bid)

    # Сохраняем текущие данные
    task.current_position = current_position
    if current_bid is not None:
        task.current_price = current_bid
    task.save(update_fields=['current_position', 'current_price'])

    log(
        task,
        f"📍 Позиция: {current_position or '?'} | "
        f"Ставка: {current_bid or '?'}₽ | "
        f"Цель: {task.target_position_min}–{task.target_position_max}"
    )

    # --- Принимаем решение ---
    target_min = task.target_position_min
    target_max = task.target_position_max

    if current_position is None or current_position > target_max:
        # Объявление ниже цели — ПОВЫШАЕМ
        # Находим минимальную ставку для достижения target_min
        needed_bid = find_bid_for_position(bids, target_min)

        if needed_bid is None:
            log(task, "Не удалось определить нужную ставку", 'ERROR')
        else:
            # Ограничиваем диапазоном пользователя
            new_price = max(min_price, min(needed_bid, max_price))

            if current_bid is not None and new_price <= float(current_bid or 0):
                # Уже достаточно или выше — просто логируем
                log(task, f"Ставка {current_bid}₽ уже достаточна для цели", 'INFO')
            elif new_price >= max_price and current_bid and float(current_bid) >= max_price:
                log(task, f"Достигнут максимум {max_price}₽", 'WARNING')
            else:
                if set_ad_price(
                    task.ad_id, new_price, token,
                    daily_limit_rub=float(task.daily_budget)
                ):
                    log(
                        task,
                        f"↑ Повышена до {new_price}₽ "
                        f"(позиция {current_position or '?'} > {target_max})",
                        'WARNING'
                    )
                    task.current_price = new_price
                    task.save(update_fields=['current_price'])
                else:
                    log(task, f"Ошибка установки ставки {new_price}₽", 'ERROR')

    elif current_position <= target_min:
        # Объявление выше цели — ПОНИЖАЕМ (экономим)
        # Находим минимальную ставку для позиции target_max
        needed_bid = find_bid_for_position(bids, target_max)

        if needed_bid is None:
            log(task, "Не удалось определить ставку для снижения", 'WARNING')
        else:
            new_price = max(min_price, min(needed_bid, max_price))

            if current_bid is not None and new_price >= float(current_bid):
                log(task, f"Ставка уже на минимуме для цели", 'INFO')
            else:
                if set_ad_price(
                    task.ad_id, new_price, token,
                    daily_limit_rub=float(task.daily_budget)
                ):
                    log(
                        task,
                        f"↓ Понижена до {new_price}₽ "
                        f"(позиция {current_position} ≤ {target_min}, экономия)",
                        'INFO'
                    )
                    task.current_price = new_price
                    task.save(update_fields=['current_price'])
                else:
                    log(task, f"Ошибка установки ставки {new_price}₽", 'ERROR')
    else:
        # Позиция в целевом диапазоне — ничего не делаем
        log(
            task,
            f"✅ Позиция {current_position} в цели "
            f"({target_min}–{target_max}), ставка не меняется",
            'INFO'
        )

    log(task, "Цикл завершён ✔")
    _reschedule(task_id)


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
            daily_limit_rub=float(task.daily_budget)
        ):
            log(task, f"↓ Снижена до {min_price}₽ (вне расписания)", 'INFO')
            task.current_price = min_price
            task.save(update_fields=['current_price'])
    else:
        log(task, "Вне расписания, ставка уже на минимуме", 'INFO')


def _reschedule(task_id: int):
    """Планирует следующий запуск через ~5 минут."""
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
        delay = 290 + random.randint(-30, 60)
        logger.info(f"Задача {task_id} → следующий запуск через {delay}с")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} — не перепланируем (удалена/отключена)")


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

    if not task.avito_account:
        logger.error(f"[update_task_details] Нет аккаунта у задачи {task_id}")
        return

    token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret
    )
    if not token:
        logger.error(f"[update_task_details] Нет токена")
        return

    info = get_item_info(token, task.ad_id)
    if info:
        updated = []
        if info.get('title'):
            task.title = info['title']
            updated.append('title')
        if info.get('image_url'):
            task.image_url = info['image_url']
            updated.append('image_url')
        if updated:
            task.save(update_fields=updated)
            logger.info(f"[update_task_details] ✅ {task_id}: «{task.title}»")
    else:
        logger.warning(f"[update_task_details] Нет данных для {task.ad_id}")
