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
    PROXY_POOL, get_avito_access_token, get_current_ad_price,
    set_ad_price, rotate_proxy_ip, get_random_proxy,
    get_other_proxy, get_ad_info_by_api
)
from .models import BiddingTask, TaskLog

logger = logging.getLogger(__name__)


# ============================================================
# ПАРСЕР ПОЗИЦИИ
# ============================================================

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Парсер с чередованием прокси.
    3 попытки, каждая через ДРУГОЙ прокси.
    """
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) '
        'Gecko/20100101 Firefox/122.0',
    ]

    max_retries = 3
    last_proxy_port = None

    for attempt in range(max_retries):
        # === КЛЮЧЕВОЕ: каждая попытка — ДРУГОЙ прокси ===
        if last_proxy_port:
            proxies, proxy_used = get_other_proxy(last_proxy_port)
        else:
            proxies, proxy_used = get_random_proxy()

        last_proxy_port = proxy_used['port']

        headers = {
            'User-Agent': random.choice(user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;'
                      'q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

        try:
            time.sleep(random.uniform(1, 3))

            logger.info(
                f"[PARSER] Попытка {attempt+1}/{max_retries} "
                f"ad_id={ad_id} прокси={proxy_used['port']}"
            )
            response = requests.get(
                search_url, headers=headers,
                proxies=proxies, timeout=25
            )

            # 403 — заблокирован
            if response.status_code == 403:
                logger.warning(
                    f"[PARSER] 403 прокси {proxy_used['port']}"
                )
                rotate_proxy_ip(proxy_used)
                time.sleep(random.uniform(2, 4))
                continue

            # 429 — слишком много запросов
            if response.status_code == 429:
                logger.warning(
                    f"[PARSER] 429 прокси {proxy_used['port']}"
                )
                rotate_proxy_ip(proxy_used)
                time.sleep(random.uniform(2, 4))
                continue

            response.raise_for_status()

            # CAPTCHA
            if 'captcha' in response.text.lower():
                logger.warning(
                    f"[PARSER] CAPTCHA прокси {proxy_used['port']}"
                )
                rotate_proxy_ip(proxy_used)
                time.sleep(random.uniform(2, 4))
                continue

            # Короткий ответ
            if len(response.text) < 5000:
                logger.warning(
                    f"[PARSER] Короткий ответ ({len(response.text)})"
                )
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            all_ads = soup.find_all('div', {'data-marker': 'item'})
            logger.info(f"[PARSER] Найдено {len(all_ads)} объявлений")

            if not all_ads:
                logger.warning("[PARSER] Нет объявлений на странице")
                continue

            for index, ad_element in enumerate(all_ads):
                if ad_element.get('data-item-id') == str(ad_id):
                    position = index + 1
                    title = "Название не найдено"
                    image_url = None

                    title_tag = ad_element.find(
                        'a', {'data-marker': 'item-title'}
                    )
                    if title_tag:
                        title = title_tag.text.strip()

                    img_tag = ad_element.find('img')
                    if img_tag:
                        image_url = (
                            img_tag.get('src') or img_tag.get('data-src')
                        )

                    logger.info(
                        f"[PARSER] ✅ ad_id={ad_id} позиция={position}"
                    )
                    return {
                        "position": position,
                        "title": title,
                        "image_url": image_url,
                    }

            logger.warning(
                f"[PARSER] ad_id={ad_id} не найдено "
                f"среди {len(all_ads)} результатов"
            )
            return None

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[PARSER] Ошибка попытка {attempt+1} "
                f"прокси {proxy_used['port']}: {e}"
            )
            rotate_proxy_ip(proxy_used)
            time.sleep(random.uniform(2, 4))

        except Exception as e:
            logger.error(f"[PARSER] Неожиданная ошибка: {e}", exc_info=True)

    logger.error(
        f"[PARSER] Все {max_retries} попыток провалились ad_id={ad_id}"
    )
    return None


# ============================================================
# ПРОВЕРКА РАСПИСАНИЯ
# ============================================================

def is_time_in_schedule(schedule_data) -> bool:
    """
    Проверяет, попадает ли текущее время в расписание.
    Если расписание пустое — всегда True.
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


# ============================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ
# ============================================================

def _reschedule(task_id: int, min_delay: int = 60, max_delay: int = 180):
    """Перепланирует задачу с рандомной задержкой."""
    try:
        task = BiddingTask.objects.get(id=task_id)
        if task.is_active:
            delay = random.randint(min_delay, max_delay)
            logger.info(f"[SCHEDULE] Задача {task_id} перезапустится через {delay} сек")
            run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
    except BiddingTask.DoesNotExist:
        pass


# ============================================================
# ОСНОВНАЯ ЗАДАЧА БИДДЕРА
# ============================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_bidding_for_task(self, task_id: int):
    """
    Биддер: получает позицию, корректирует ставку, перепланирует себя.
    """
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена.")
        return

    def log(message, level='INFO'):
        TaskLog.objects.create(task=task, message=message, level=level)

    try:
        # --- Обновляем last_run сразу ---
        task.last_run = timezone.now()
        task.save(update_fields=['last_run'])

        # --- Защита от слишком частых запусков ---
        last_log = TaskLog.objects.filter(
            task=task, message__startswith="Запуск биддера"
        ).order_by('-timestamp').first()

        if last_log and (timezone.now() - last_log.timestamp).total_seconds() < 90:
            logger.info(f"Задача {task_id} слишком частая — пропуск")
            _reschedule(task_id, 120, 240)
            return

        # --- 1. ПОЛУЧЕНИЕ ТОКЕНА ---
        if not task.avito_account:
            log("Задача не привязана к аккаунту Avito.", level='ERROR')
            _reschedule(task_id, 180, 360)
            return

        access_token = get_avito_access_token(
            task.avito_account.avito_client_id,
            task.avito_account.avito_client_secret
        )
        if not access_token:
            log("Не удалось получить токен.", level='ERROR')
            _reschedule(task_id, 180, 360)
            return

        # --- 2. ПРОВЕРКА РАСПИСАНИЯ ---
        if not is_time_in_schedule(task.schedule):
            logger.info(f"Задача {task_id} вне расписания.")
            current_price = get_current_ad_price(task.ad_id, access_token)
            min_price = float(task.min_price)

            if current_price is not None and float(current_price) > min_price:
                if set_ad_price(task.ad_id, min_price, access_token,
                                daily_limit_rub=float(task.daily_budget)):
                    log(f"↓ Снижена до {min_price} ₽ (вне расписания)")
                    task.current_price = min_price
                    task.save(update_fields=['current_price'])

            _reschedule(task_id, 180, 360)
            return

        # --- 3. ОСНОВНАЯ ЛОГИКА ---
        log(f"Запуск биддера для {task.ad_id}")

        # Парсинг без принудительной ротации — экономим время
        time.sleep(random.uniform(1, 3))
        ad_data = get_ad_position(task.search_url, task.ad_id)

        if ad_data is None:
            # === Объявление НЕ найдено в выдаче ===
            log("Ошибка парсера: объявление не найдено в топ-50.", level='ERROR')
            task.current_position = None

            if task.freeze_price_if_not_found:
                log("Цена заморожена (согласно настройке).", level='WARNING')
            else:
                current_price_from_db = task.current_price

                if current_price_from_db is None:
                    new_price = float(task.min_price)
                    msg = f"↑ (Первый толчок) Установлена {new_price} ₽."
                else:
                    new_price = float(current_price_from_db) + float(task.bid_step)
                    msg = f"↑ (Вслепую) Повышена до {new_price} ₽."

                if new_price <= float(task.max_price):
                    if set_ad_price(task.ad_id, new_price, access_token,
                                    daily_limit_rub=float(task.daily_budget)):
                        log(msg, level='WARNING')
                        task.current_price = new_price
                    else:
                        log(f"Ошибка установки цены {new_price} ₽.", level='ERROR')
                else:
                    log(f"Достигнут максимум {task.max_price} ₽.", level='WARNING')

            task.save(update_fields=['current_position', 'current_price'])

        else:
            # === Объявление НАЙДЕНО ===
            position = ad_data.get("position")
            current_price = get_current_ad_price(task.ad_id, access_token)

            task.current_position = position
            if current_price is not None:
                task.current_price = current_price

            # Обновляем title/image если получили из парсера
            if ad_data.get("title") and ad_data["title"] != "Название не найдено":
                task.title = ad_data["title"]
            if ad_data.get("image_url"):
                task.image_url = ad_data["image_url"]

            task.save(update_fields=[
                'current_position', 'current_price', 'title', 'image_url'
            ])

            log(
                f"Позиция: {position} "
                f"(цель {task.target_position_min}–{task.target_position_max}), "
                f"ставка: {current_price or '—'} ₽"
            )

            if current_price is None:
                log("Не удалось получить текущую цену.", level='ERROR')
            else:
                if position > task.target_position_max:
                    # Нужно повышать
                    new_price = float(current_price) + float(task.bid_step)
                    if new_price <= float(task.max_price):
                        success = set_ad_price(
                            task.ad_id, new_price, access_token,
                            daily_limit_rub=float(task.daily_budget)
                        )
                        if success:
                            log(
                                f"↑ Повышена до {new_price} ₽ "
                                f"(позиция {position} > {task.target_position_max})",
                                level='WARNING'
                            )
                            task.current_price = new_price
                            task.save(update_fields=['current_price'])
                        else:
                            log("Ошибка повышения ставки", level='ERROR')
                    else:
                        log(
                            f"Достигнут максимум {task.max_price} ₽",
                            level='WARNING'
                        )

                elif position <= task.target_position_min:
                    # Можно экономить
                    new_price = float(current_price) - float(task.bid_step)
                    if new_price >= float(task.min_price):
                        success = set_ad_price(
                            task.ad_id, new_price, access_token,
                            daily_limit_rub=float(task.daily_budget)
                        )
                        if success:
                            log(
                                f"↓ Понижена до {new_price} ₽ "
                                f"(позиция {position} в норме)",
                                level='INFO'
                            )
                            task.current_price = new_price
                            task.save(update_fields=['current_price'])
                        else:
                            log("Ошибка понижения ставки", level='ERROR')
                    else:
                        log(
                            f"Достигнут минимум {task.min_price} ₽",
                            level='INFO'
                        )
                else:
                    # Позиция в целевом диапазоне — не трогаем
                    log(
                        f"✅ Позиция {position} в норме — ставка без изменений",
                        level='INFO'
                    )

        log("Цикл завершён")

    except Exception as e:
        logger.error(f"[BIDDER] Ошибка задачи {task_id}: {e}", exc_info=True)
        log(f"Критическая ошибка: {str(e)[:200]}", level='ERROR')

    finally:
        # ВСЕГДА перепланируем, даже при ошибке
        _reschedule(task_id, 60, 180)


# ============================================================
# ОБНОВЛЕНИЕ ДЕТАЛЕЙ ЧЕРЕЗ API (без парсинга)
# ============================================================

@shared_task
def update_task_details(task_id: int):
    """
    Подгружает заголовок и фото через API Avito.
    Парсинг поиска не нужен.
    """
    try:
        task = BiddingTask.objects.get(pk=task_id)
    except BiddingTask.DoesNotExist:
        return

    if not task.avito_account:
        logger.warning(f"[DETAILS] Задача {task_id}: нет аккаунта")
        return

    access_token = get_avito_access_token(
        task.avito_account.avito_client_id,
        task.avito_account.avito_client_secret,
    )
    if not access_token:
        logger.error(f"[DETAILS] Задача {task_id}: нет токена")
        return

    ad_info = get_ad_info_by_api(task.ad_id, access_token)
    if ad_info:
        task.title = ad_info.get('title', 'Без названия')
        task.image_url = ad_info.get('image_url')
        task.save(update_fields=['title', 'image_url'])
        logger.info(
            f"[DETAILS] Задача {task_id}: "
            f"'{task.title[:40]}', фото: {'да' if task.image_url else 'нет'}"
        )
    else:
        logger.warning(f"[DETAILS] Задача {task_id}: данные не получены")


# ============================================================
# WATCHDOG — ОЖИВЛЕНИЕ ЗАВИСШИХ ЗАДАЧ
# ============================================================

@shared_task
def revive_stale_tasks():
    """
    Находит задачи которые давно не запускались и перезапускает их.
    Запускается через Celery Beat каждые 5 минут.
    """
    stale_minutes = 10
    threshold = timezone.now() - timezone.timedelta(minutes=stale_minutes)

    stale_tasks = BiddingTask.objects.filter(
        is_active=True
    ).filter(
        # last_run давно ИЛИ никогда не запускалась
        last_run__lt=threshold
    ) | BiddingTask.objects.filter(
        is_active=True,
        last_run__isnull=True
    )

    count = 0
    for task in stale_tasks:
        if task.last_run is None:
            logger.warning(
                f"🆕 Запуск задачи {task.id} (ad_id={task.ad_id}) "
                f"— никогда не запускалась"
            )
        else:
            minutes_ago = int(
                (timezone.now() - task.last_run).total_seconds() / 60
            )
            logger.warning(
                f"🔄 Перезапуск зависшей задачи {task.id} "
                f"(ad_id={task.ad_id}, {minutes_ago} мин назад)"
            )

        run_bidding_for_task.apply_async(
            args=[task.id],
            countdown=random.randint(5, 30)
        )
        count += 1

    logger.info(f"✅ Watchdog: оживлено {count} задач")
    return count