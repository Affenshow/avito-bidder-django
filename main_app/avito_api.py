# main_app/avito_api.py

import requests
import logging
import random
import time
from typing import Union, Dict, List

logger = logging.getLogger(__name__)

# --- Прокси-пул (оба твоих прокси) ---
PROXY_POOL = [
    {
        'user': 'uKuNaf',
        'pass': 'FAjEC5HeK7yt',
        'host': 'mproxy.site',
        'port': 17563,
        'change_ip_url': 'https://changeip.mobileproxy.space/?proxy_key=65a15a75eb565bba6e220d15559005e3'
    },
    {
        'user': 'vuU1DY',
        'pass': 'apsYVEZRaY7c',
        'host': 'mproxy.site',
        'port': 11289,
        'change_ip_url': 'https://changeip.mobileproxy.space/?proxy_key=7db42d70377c063ba427f4487f63aa6f'
    },
    # Если купишь третий — просто добавь сюда словарь
]

def get_random_proxy() -> Dict:
    """Возвращает случайный прокси из пула"""
    proxy = random.choice(PROXY_POOL)
    return {
        'http': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
        'https': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
    }, proxy  # возвращаем и прокси-словарь, чтобы знать, какой IP менять


def rotate_proxy_ip(proxy: Dict):
    """Смена IP для конкретного прокси"""
    try:
        logger.info(f"[PROXY] Смена IP для порта {proxy['port']}...")
        response = requests.get(proxy['change_ip_url'], timeout=10)
        response.raise_for_status()
        logger.info(f"[PROXY] IP успешно сменён для порта {proxy['port']}. Ответ: {response.text}")
        time.sleep(5)  # даём время прокси на смену IP
    except Exception as e:
        logger.error(f"[PROXY] Ошибка смены IP для порта {proxy['port']}: {e}")


# --- Основные эндпоинты Avito ---
TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self'
CORE_BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance'
CPA_BALANCE_URL = 'https://api.avito.ru/cpa/v3/balanceInfo'

# --- Эндпоинты для ставок CPxPromo ---
GET_BIDS_URL_TPL = 'https://api.avito.ru/cpxpromo/1/getBids/{item_id}'
SET_MANUAL_BID_URL = 'https://api.avito.ru/cpxpromo/1/setManual'

def get_avito_access_token(client_id: str, client_secret: str) -> Union[str, None]:
    """Обмен client_id и client_secret на access_token."""
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials'
    }
    try:
        logger.info(f"[TOKEN] Запрос для client_id: {client_id[:8]}...")
        response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token:
            logger.info("[TOKEN] Успех: токен получен")
            return access_token
        logger.error(f"[TOKEN] access_token не найден в ответе: {token_data}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[TOKEN] Ошибка получения токена: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"[TOKEN] Ответ сервера: {e.response.text}")
        return None


def get_avito_user_id(access_token: str) -> Union[int, None]:
    """Получает ID текущего пользователя Avito."""
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get(USER_INFO_URL, headers=headers, timeout=10)
        response.raise_for_status()
        user_id = response.json().get('id')
        if user_id:
            logger.info(f"[USER] ID пользователя: {user_id}")
            return user_id
        logger.error("[USER] ID пользователя не найден в ответе")
        return None
    except Exception as e:
        logger.error(f"[USER] Ошибка получения ID: {e}")
        return None


def get_balances(access_token: str, user_id: int) -> Dict:
    """Получает баланс кошелька и аванс (CPA)."""
    result = {'real': None, 'bonus': None}
    headers = {'Authorization': f'Bearer {access_token}'}

    try:
        url = CORE_BALANCE_URL_TPL.format(user_id=user_id)
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        result['real'] = resp.json().get('real', 0)
    except Exception as e:
        logger.warning(f"[BALANCE] Ошибка реального баланса: {e}")

    try:
        cpa_headers = {**headers, 'X-Source': 'AvitoBidder'}
        resp = requests.post(CPA_BALANCE_URL, headers=cpa_headers, json={}, timeout=10)
        resp.raise_for_status()
        result['bonus'] = resp.json().get('balance', 0) / 100
    except Exception as e:
        logger.warning(f"[BALANCE] Ошибка CPA-аванса: {e}")

    return result


def get_current_ad_price(ad_id: int, access_token: str) -> Union[float, None]:
    if not access_token:
        return None

    headers = {'Authorization': f'Bearer {access_token}'}
    url = GET_BIDS_URL_TPL.format(item_id=ad_id)

    max_retries = 3
    proxy_used = None  # ← добавляем

    for attempt in range(max_retries):
        proxies, proxy_used = get_random_proxy()
        try:
            logger.info(f"[STAVKA] Попытка {attempt+1}/{max_retries} через прокси {proxy_used['port']}")
            response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            bid_in_kopecks = data.get('manual', {}).get('bidPenny')
            if bid_in_kopecks is not None:
                price = float(bid_in_kopecks) / 100
                logger.info(f"[STAVKA] Текущая цена: {price} ₽")
                return price
            
            logger.warning("[STAVKA] Поле manual.bidPenny не найдено")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"[STAVKA] Ошибка на попытке {attempt+1} (прокси {proxy_used['port'] if proxy_used else 'неизвестный'}): {e}")
            if proxy_used is not None:
                rotate_proxy_ip(proxy_used)
            time.sleep(5)

    logger.error("[STAVKA] Все попытки провалились")
    return None


def set_ad_price(ad_id: int, new_price: float, access_token: str, daily_limit_rub: float = None) -> bool:
    """
    Устанавливает новую цену просмотра и (опционально) дневной лимит трат.
    """
    if not access_token:
        logger.error("[SET] Нет access_token — установка невозможна")
        return False

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }

    ACTION_TYPE_ID_FOR_VIEWS = 5

    body = {
        "itemID": ad_id,
        "actionTypeID": ACTION_TYPE_ID_FOR_VIEWS,
        "bidPenny": int(new_price * 100)
    }

    log_message = f"Установка ставки {new_price} ₽"

    if daily_limit_rub is not None:
        if daily_limit_rub > 0:
            limit_kopecks = int(daily_limit_rub * 100)
            body["dailyBudgetPenny"] = limit_kopecks
            log_message += f" + лимита {daily_limit_rub} ₽"
        else:
            log_message += " (лимит 0 — параметр не передаётся)"

    proxies, proxy_used = get_random_proxy()

    try:
        logger.info(f"[SET] {log_message}")
        logger.info(f"[SET] Отправка запроса на {SET_MANUAL_BID_URL} через прокси {proxy_used['port']} с body: {body}")
        
        response = requests.post(SET_MANUAL_BID_URL, headers=headers, json=body, proxies=proxies, timeout=15)
        response.raise_for_status()
        
        logger.info(f"[SET] УСПЕХ! Статус: {response.status_code}, Ответ: {response.text or 'пусто'}")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"[SET] Ошибка установки ставки/лимита через прокси {proxy_used['port']}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            status = e.response.status_code
            text = e.response.text or 'пусто'
            logger.error(f"[SET] Статус: {status}, Ответ сервера: {text}")
            if status in (400, 403):
                logger.warning("[SET] Авито отклонил запрос — возможно, параметр dailyBudgetPenny не поддерживается или IP заблокирован")
        rotate_proxy_ip(proxy_used)  # меняем IP при ошибке
        return False


def rotate_proxy_ip(proxy: Dict):
    """Смена IP для конкретного прокси"""
    try:
        logger.info(f"[PROXY] Смена IP для порта {proxy['port']}...")
        response = requests.get(proxy['change_ip_url'], timeout=10)
        response.raise_for_status()
        logger.info(f"[PROXY] IP успешно сменён для порта {proxy['port']}. Ответ: {response.text}")
        time.sleep(5)  # пауза на смену IP
    except Exception as e:
        logger.error(f"[PROXY] Ошибка смены IP для порта {proxy['port']}: {e}")


def get_user_ads(access_token: str) -> Union[List[Dict], None]:
    """
    Получает список активных объявлений пользователя.
    """
    user_id = get_avito_user_id(access_token)
    if not user_id:
        logger.error("[ADS] Не удалось получить user_id")
        return None

    url = f"https://api.avito.ru/core/v1/accounts/{user_id}/ads/"
    headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}

    proxies, proxy_used = get_random_proxy()

    try:
        logger.info(f"[ADS] Запрос списка объявлений пользователя {user_id} через прокси {proxy_used['port']}...")
        response = requests.get(url, headers=headers, proxies=proxies, timeout=20)
        response.raise_for_status()
        data = response.json()
        ads = data.get('resources', [])

        formatted = []
        for ad in ads:
            if ad.get('status') == 'active':
                formatted.append({
                    'id': ad.get('id'),
                    'title': ad.get('title', 'Без названия')
                })

        logger.info(f"[ADS] Найдено {len(formatted)} активных объявлений")
        return formatted

    except requests.exceptions.RequestException as e:
        logger.error(f"[ADS] Ошибка запроса объявлений через прокси {proxy_used['port']}: {e}")
        rotate_proxy_ip(proxy_used)
        return None