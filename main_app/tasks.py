import logging
import platform
import json
from typing import Union, Dict
from datetime import datetime
from seleniumwire import webdriver  # <-- использование selenium-wire с прокси
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

from .models import BiddingTask, TaskLog
from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price

logger = logging.getLogger(__name__)

def get_ad_position(search_url: str, ad_id: int, proxy_login=None, proxy_pass=None, proxy_host=None, proxy_port=None) -> Union[Dict, None]:
    """
    Парсит страницу с помощью Selenium-Wire через мобильный прокси.
    Можно передать индивидуальные прокси для каждого пользователя.
    """
    # --- Настройки прокси ---
    proxy_login = proxy_login or "aZ2UCa"
    proxy_pass = proxy_pass or "KEVhaQ2MaR5S"
    proxy_host = proxy_host or "185.234.59.17"
    proxy_port = proxy_port or 20379

    proxy_url = f'http://{proxy_login}:{proxy_pass}@{proxy_host}:{proxy_port}'
    proxy_options = {
        'proxy': {
            'http': proxy_url,
            'https': proxy_url,
            'no_proxy': 'localhost,127.0.0.1'
        }
    }

    # --- Настройки chrome ---
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    if platform.system() == "Linux":
        chrome_options.binary_location = "/usr/bin/google-chrome-stable"

    driver = None
    try:
        # --- "УМНАЯ" ИНИЦИАЛИЗАЦИЯ v2.0 ---
        if platform.system() == "Linux":
            logger.info("--- [SELENIUM] Linux. Используется драйвер из /usr/local/bin/chromedriver.")
            # Явно указываем путь к нашему скачанному драйверу
            service = ChromeService(executable_path="/usr/local/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=chrome_options, seleniumwire_options=proxy_options)
        else: # Для Windows
            logger.info("--- [SELENIUM] Windows. Используется webdriver-manager.")
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options, seleniumwire_options=proxy_options)

        # Логируем внешний IP через прокси для самопроверки
        driver.get('https://api.ipify.org?format=json')
        logger.info(f"[PROXY CHECK] Внешний IP через прокси: {driver.page_source}")

        driver.get(search_url)
        all_ads = driver.find_elements(By.CSS_SELECTOR, "div[data-marker='item']")
        logger.info(f"--- [SELENIUM-WIRE] Найдено {len(all_ads)} объявлений на странице.")

        if not all_ads:
            return None

        for index, ad_element in enumerate(all_ads):
            if ad_element.get_attribute('data-item-id') == str(ad_id):
                position = index + 1
                title = "Название не найдено"
                image_url = None
                try:
                    title_tag = ad_element.find_element(By.CSS_SELECTOR, "a[data-marker='item-title']")
                    title = title_tag.text
                except Exception:
                    logger.warning("Не удалось найти заголовок.")
                try:
                    img_tag = ad_element.find_element(By.TAG_NAME, "img")
                    image_url = img_tag.get_attribute('src')
                except Exception:
                    logger.warning("Не удалось найти картинку.")
                logger.info(f"--- [SELENIUM-WIRE] Найдено объявление {ad_id} на позиции {position}! ---")
                return {"position": position, "title": title, "image_url": image_url}

        logger.warning(f"--- [SELENIUM-WIRE] Объявление {ad_id} НЕ найдено на странице.")
        return None

    except Exception as e:
        logger.error(f"--- [SELENIUM-WIRE] КРИТИЧЕСКАЯ ОШИБКА: {e}")
        if driver:
            driver.save_screenshot("debug_ERROR_selenium.png")
            logger.info("--- [SELENIUM-WIRE] Скриншот ОШИБКИ сохранен.")
        return None
    finally:
        if driver:
            driver.quit()
            logger.info("--- [SELENIUM-WIRE] Драйвер Chrome закрыт.")

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

@shared_task
def run_bidding_for_task(task_id: int):
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.warning(f"Задача #{task_id} больше не существует или выключена. Пропускаем.")
        return

    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача #{task_id} неактивна по расписанию. Пропускаем.")
        return

    TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")

    profile = task.user.profile
    if not profile.avito_client_id or not profile.avito_client_secret:
        TaskLog.objects.create(task=task, message="API-ключи не настроены. Пропуск.", level='ERROR')
        return

    access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
    if not access_token:
        TaskLog.objects.create(task=task, message="Не удалось получить токен доступа. Проверьте API-ключи.", level='ERROR')
        return

    # Можно дополнительно здесь выбирать индивидуальный прокси:
    # proxy_login, proxy_pass, proxy_host, proxy_port = get_user_proxy(task.user)
    ad_data = get_ad_position(task.search_url, task.ad_id)  # , proxy_login, proxy_pass, proxy_host, proxy_port)
    if ad_data is None:
        TaskLog.objects.create(task=task, message="Не удалось получить информацию с Avito (ошибка парсера).", level='ERROR')
        return

    position = ad_data.get("position")
    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: [{task.target_position_min} - {task.target_position_max}].")

    current_price = get_current_ad_price(task.ad_id, access_token)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену через API.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

    if position > task.target_position_max:
        new_price = float(current_price) + float(task.bid_step)
        if new_price <= float(task.max_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. Ставка повышена до {new_price} ₽.", level='WARNING')
            else:
                TaskLog.objects.create(task=task, message=f"Позиция {position} > {task.target_position_max}. НЕ УДАЛОСЬ повысить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута макс. ставка {task.max_price} ₽.", level='WARNING')
    elif position < task.target_position_min:
        new_price = float(current_price) - float(task.bid_step)
        if new_price >= float(task.min_price):
            success = set_ad_price(task.ad_id, new_price, access_token)
            if success:
                TaskLog.objects.create(task=task, message=f"Экономия: позиция {position} < {task.target_position_min}. Ставка понижена до {new_price} ₽.", level='INFO')
            else:
                TaskLog.objects.create(task=task, message=f"Экономия: НЕ УДАЛОСЬ понизить ставку.", level='ERROR')
        else:
            TaskLog.objects.create(task=task, message=f"Достигнута мин. ставка {task.min_price} ₽.", level='INFO')
    else:
        TaskLog.objects.create(task=task, message="Позиция в целевом диапазоне. Ставка не изменена.")

    TaskLog.objects.create(task=task, message="Биддер завершил работу.")

@shared_task
def trigger_all_active_tasks():
    logger.info(">>> ПЛАНИРОВЩИК: Поиск активных задач...")
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