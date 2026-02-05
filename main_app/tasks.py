import logging
import time
import random
import json
import multiprocessing as mp
# import platform
# import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
# from selenium...                             # всё, что связано с selenium, больше не нужно

from celery import shared_task

from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price, rotate_proxy_ip
from .models import BiddingTask, TaskLog
from playwright_stealth import stealth_sync

# ────────────────────────────────────────────────
# Импорты для Playwright — раскомментируй после установки
# pip install playwright
# playwright install chromium
# ────────────────────────────────────────────────
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Получает позицию объявления с помощью Playwright.
    Возвращает словарь с позицией, заголовком и URL картинки или None.
    """
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    ]

    # Твои прокси-данные
    proxy_user = "uKuNaf"
    proxy_pass = "FAjEC5HeK7yt"
    proxy_host = "mproxy.site"
    proxy_port = 17563

    proxy_config = {
        "server": f"http://{proxy_host}:{proxy_port}",
        "username": proxy_user,
        "password": proxy_pass,
    }

    try:
        with sync_playwright() as p:
            logger.info(f"[PLAYWRIGHT] Запуск браузера для {search_url}")

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )

            context = browser.new_context(
                user_agent=random.choice(user_agents),
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                proxy=proxy_config,                     # прокси здесь
                ignore_https_errors=True,               # если будут проблемы с сертификатами
            )

            page = context.new_page()
            stealth_sync(page)

            # Переход на страницу
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

            if not response or response.status >= 400:
                logger.warning(f"[PLAYWRIGHT] Плохой ответ: {response.status if response else 'нет ответа'}")
                browser.close()
                return None

            # Ждём появления элементов объявлений
            try:
                page.wait_for_selector('[data-marker="item"]', timeout=30000)
            except Exception:
                logger.warning("[PLAYWRIGHT] Не дождались элементов объявлений")
                browser.close()
                return None

            # Имитация человеческого поведения
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
            time.sleep(random.uniform(1.5, 4.2))

            # Получаем HTML после рендеринга
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')

            all_ads = soup.find_all('div', {'data-marker': 'item'})
            logger.info(f"[PLAYWRIGHT] Найдено {len(all_ads)} объявлений")

            if not all_ads:
                browser.close()
                return None

            for index, ad_element in enumerate(all_ads):
                item_id = ad_element.get('data-item-id')
                if item_id == str(ad_id):
                    position = index + 1
                    title = "Название не найдено"
                    image_url = None

                    title_tag = ad_element.find('a', {'data-marker': 'item-title'})
                    if title_tag:
                        title = title_tag.get_text(strip=True)

                    # Картинка — разные селекторы могут быть, это один из вариантов
                    img = ad_element.select_one('img[src^="https://"]')
                    if img and img.get('src'):
                        image_url = img['src']

                    logger.info(f"[PLAYWRIGHT] Обнаружено объявление {ad_id} на позиции {position}")

                    browser.close()
                    return {
                        "position": position,
                        "title": title,
                        "image_url": image_url
                    }

            logger.warning(f"[PLAYWRIGHT] Объявление {ad_id} не найдено на странице")
            browser.close()
            return None

    except Exception as e:
        logger.error(f"[PLAYWRIGHT] Критическая ошибка: {e}", exc_info=True)
        if 'browser' in locals():
            browser.close()
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


@shared_task(bind=True, max_retries=5, default_retry_delay=300)  # увеличил max_retries и задержку
def run_bidding_for_task(self, task_id: int):
    """
    Основная логика биддера.
    После выполнения планирует себя на следующий запуск через ~5 минут ± рандом.
    """
    import time
    from datetime import datetime, timedelta

    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.info(f"Задача {task_id} удалена или отключена — завершаем.")
        return

    # Проверяем расписание
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача {task_id} сейчас не по расписанию — пропускаем, но планируем следующий запуск")
    else:
        # Защита от слишком частых запусков
        last_run = TaskLog.objects.filter(
            task=task,
            message="Цикл биддера завершён"
        ).order_by('-created_at').first()

        if last_run:
            seconds_ago = (datetime.now() - last_run.created_at).total_seconds()
            if seconds_ago < 180:  # меньше 3 минут — пропускаем
                logger.info(f"Задача {task_id} запущена слишком недавно ({seconds_ago:.0f} сек назад) — пропуск")
                # всё равно планируем следующий
                task.is_active = True  # на случай, если кто-то снял флаг
            else:
                TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")
        else:
            TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")

        profile = task.user.profile
        if not profile.avito_client_id or not profile.avito_client_secret:
            TaskLog.objects.create(task=task, message="API-ключи не настроены.", level='ERROR')
        else:
            access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
            if not access_token:
                TaskLog.objects.create(task=task, message="Не удалось получить токен.", level='ERROR')
            else:
                # Опциональная ротация прокси перед парсингом
                # rotate_proxy_ip()
                # time.sleep(random.uniform(5, 15))  # пауза после ротации

                # Дополнительная задержка перед запуском браузера
                time.sleep(random.uniform(8, 25))

                # Парсинг позиции (здесь используется твоя текущая функция на Playwright)
                ad_data = None
                for attempt in range(1, 3):  # 2 попытки
                    logger.info(f"[Задача {task_id}] Попытка парсинга позиции №{attempt}")
                    ad_data = get_ad_position(task.search_url, task.ad_id)
                    if ad_data is not None:
                        break
                    time.sleep(random.uniform(20, 60))  # пауза между попытками

                if ad_data is None:
                    TaskLog.objects.create(
                        task=task,
                        message="Ошибка парсера позиции после всех попыток.",
                        level='ERROR'
                    )
                else:
                    position = ad_data.get("position")
                    TaskLog.objects.create(
                        task=task,
                        message=f"Текущая позиция: {position}. Цель: [{task.target_position_min} – {task.target_position_max}]."
                    )

                    current_price = get_current_ad_price(task.ad_id, access_token)
                    if current_price is None:
                        TaskLog.objects.create(task=task, message="Не удалось получить цену.", level='ERROR')
                    else:
                        TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

                        # Логика изменения ставки
                        if position > task.target_position_max:
                            new_price = float(current_price) + float(task.bid_step)
                            if new_price <= float(task.max_price):
                                success = set_ad_price(task.ad_id, new_price, access_token)
                                if success:
                                    TaskLog.objects.create(
                                        task=task,
                                        message=f"↑ Повышена до {new_price} ₽ (позиция {position})",
                                        level='WARNING'
                                    )
                                else:
                                    TaskLog.objects.create(task=task, message="Не удалось повысить ставку", level='ERROR')
                            else:
                                TaskLog.objects.create(task=task, message=f"Достигнут максимум {task.max_price} ₽", level='WARNING')

                        elif position < task.target_position_min:
                            new_price = float(current_price) - float(task.bid_step)
                            if new_price >= float(task.min_price):
                                success = set_ad_price(task.ad_id, new_price, access_token)
                                if success:
                                    TaskLog.objects.create(
                                        task=task,
                                        message=f"↓ Понижена до {new_price} ₽ (экономия, позиция {position})",
                                        level='INFO'
                                    )
                                else:
                                    TaskLog.objects.create(task=task, message="Не удалось понизить ставку", level='ERROR')
                            else:
                                TaskLog.objects.create(task=task, message=f"Достигнут минимум {task.min_price} ₽", level='INFO')
                        else:
                            TaskLog.objects.create(task=task, message="Позиция в норме — ставка не менялась")

        TaskLog.objects.create(task=task, message="Цикл биддера завершён.")

    # Планируем следующий запуск
    if task.is_active:
        delay = 300 + random.randint(-120, 120)  # 180–420 сек ≈ 3–7 минут
        logger.info(f"Задача {task_id} запланирована на повтор через {delay} секунд")
        run_bidding_for_task.apply_async(args=[task_id], countdown=delay)
    else:
        logger.info(f"Задача {task_id} отключена — повторный запуск не планируется")

@shared_task
def trigger_all_active_tasks():
    logger.info(">>> ПЛАНИРОВЩИК: Поиск активных задач...")
    rotate_proxy_ip()
    time.sleep(10)
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