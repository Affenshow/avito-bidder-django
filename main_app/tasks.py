import logging
import platform
import time
import random
import json
import undetected_chromedriver as uc
import requests
from bs4 import BeautifulSoup
from typing import Union, Dict
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from .avito_api import get_avito_access_token, get_current_ad_price, set_ad_price, rotate_proxy_ip
from .models import BiddingTask, TaskLog


logger = logging.getLogger(__name__)

def get_ad_position(search_url: str, ad_id: int) -> Union[Dict, None]:
    """
    Парсит страницу с помощью "продвинутого" Selenium (undetected-chromedriver),
    используя прокси и имитацию поведения человека.
    """
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument('--disable-blink-features=AutomationControlled') # <-- Главный "анти-детект" флаг
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

    # --- Ваши настройки прокси ---
    proxy_host = "185.234.59.17"
    proxy_port = 20379
    proxy_user = "aZ2UCaK"
    proxy_pass = "EVhaQ2MaR5S"
    options.add_argument(f'--proxy-server=http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}')
    
    # Если мы на Linux (сервере), явно указываем путь к браузеру
    if platform.system() == "Linux":
        options.binary_location = "/usr/bin/google-chrome-stable"

    driver = None
    try:
        logger.info("--- [SELENIUM-PRO] Запуск...")
        driver = uc.Chrome(options=options, use_subprocess=False)
        driver.set_page_load_timeout(45)

        logger.info(f"--- [SELENIUM-PRO] Переход по URL: {search_url} ---")
        driver.get(search_url)

        # --- ИМИТАЦИЯ ЧЕЛОВЕКА ---
        logger.info("--- [SELENIUM-PRO] Имитирую поведение: жду и скроллю...")
        time.sleep(random.uniform(2.5, 4.5))
        driver.execute_script(f"window.scrollBy(0, {random.randint(400, 800)});")
        time.sleep(random.uniform(1.0, 2.5))
        # --- КОНЕЦ ИМИТАЦИИ ---

        # Ищем все блоки объявлений
        all_ads = driver.find_elements(By.CSS_SELECTOR, "div[data-marker='item']")
        logger.info(f"--- [SELENIUM-PRO] Найдено {len(all_ads)} объявлений на странице.")

        if not all_ads:
            driver.save_screenshot("debug_no_ads_found.png")
            return None

        for index, ad_element in enumerate(all_ads):
            if ad_element.get_attribute('data-item-id') == str(ad_id):
                position = index + 1
                title, image_url = "Название не найдено", None
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
                
                logger.info(f"--- [SELENIUM-PRO] Найдено объявление {ad_id} на позиции {position}! ---")
                return {"position": position, "title": title, "image_url": image_url}
        
        logger.warning(f"--- [SELENIUM-PRO] Объявление {ad_id} НЕ найдено на странице.")
        driver.save_screenshot("debug_ad_not_found.png")
        return None
    
    except Exception as e:
        logger.error(f"--- [SELENIUM-PRO] КРИТИЧЕСКАЯ ОШИБКА: {e}")
        if driver:
            driver.save_screenshot("debug_FATAL_ERROR.png")
        return None
        
    finally:
        if driver:
            driver.quit()
            logger.info("--- [SELENIUM-PRO] Драйвер Chrome закрыт.")

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
    """
    Основная, полностью рабочая логика биддера.
    """
    # --- 1. Получаем задачу и проверяем, активна ли она ---
    try:
        task = BiddingTask.objects.get(id=task_id, is_active=True)
    except BiddingTask.DoesNotExist:
        logger.warning(f"Задача #{task_id} больше не существует или выключена. Пропускаем.")
        return

    # --- 2. Проверяем расписание ---
    if not is_time_in_schedule(task.schedule):
        logger.info(f"Задача #{task_id} неактивна по расписанию. Пропускаем.")
        return

    TaskLog.objects.create(task=task, message=f"Запуск биддера для объявления {task.ad_id}.")

    # --- 3. Получаем токен доступа к API ---
    profile = task.user.profile
    if not profile.avito_client_id or not profile.avito_client_secret:
        TaskLog.objects.create(task=task, message="API-ключи не настроены. Пропуск.", level='ERROR')
        return
    
    access_token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
    if not access_token:
        TaskLog.objects.create(task=task, message="Не удалось получить токен доступа.", level='ERROR')
        return

    # --- 4. МЕНЯЕМ IP-АДРЕС ПРОКСИ (НОВЫЙ ШАГ) ---
    ip_changed = rotate_proxy_ip()
    if ip_changed:
        TaskLog.objects.create(task=task, message="IP-адрес прокси успешно сменен.")
        time.sleep(10) # Даем 10 секунд на "прогрев" нового IP
    else:
        TaskLog.objects.create(task=task, message="Не удалось сменить IP-адрес прокси. Работаем со старым.", level='WARNING')


    # --- 5. Получаем актуальную информацию с Avito ---
    ad_data = get_ad_position(task.search_url, task.ad_id)
    if ad_data is None:
        TaskLog.objects.create(task=task, message="Не удалось получить информацию с Avito (ошибка парсера).", level='ERROR')
        return
    
    position = ad_data.get("position")
    TaskLog.objects.create(task=task, message=f"Текущая позиция: {position}. Цель: [{task.target_position_min} - {task.target_position_max}].")

    # --- 6. Получаем реальную текущую цену через API ---
    current_price = get_current_ad_price(task.ad_id, access_token)
    if current_price is None:
        TaskLog.objects.create(task=task, message="Не удалось получить текущую цену через API.", level='ERROR')
        return
    TaskLog.objects.create(task=task, message=f"Текущая ставка: {current_price} ₽.")

    # --- 7. "Умный" алгоритм биддера ---
    if position > task.target_position_max:
        # --- ЛОГИКА ПОВЫШЕНИЯ ---
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
        # --- ЛОГИКА ПОНИЖЕНИЯ ---
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