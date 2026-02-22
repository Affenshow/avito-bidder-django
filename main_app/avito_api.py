# main_app/avito_api.py

import requests
import logging
import random
import time
from typing import Union, Dict, List

logger = logging.getLogger(__name__)


# =============================================================
# ПРОКСИ-ПУЛ (мобильные)
# =============================================================

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
]

# Время последней ротации для каждого прокси
_last_rotation = {}


def get_random_proxy(exclude_port=None) -> tuple:
    """Возвращает (proxies_dict, proxy_info). Можно исключить порт."""
    available = [p for p in PROXY_POOL if p['port'] != exclude_port]
    if not available:
        available = PROXY_POOL
    proxy = random.choice(available)
    return {
        'http': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
        'https': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
    }, proxy


def rotate_proxy_ip(proxy: Dict):
    """Смена IP — не чаще 1 раза в 60 сек."""
    port = proxy['port']
    now = time.time()
    last = _last_rotation.get(port, 0)

    if now - last < 60:
        logger.info(f"[PROXY] Порт {port} — ротация была {int(now - last)} сек назад, пропуск")
        return

    try:
        url = proxy['change_ip_url']
        if '&format=json' not in url:
            url += '&format=json'

        logger.info(f"[PROXY] Смена IP для порта {port}...")
        response = requests.get(url, timeout=10)
        _last_rotation[port] = now

        try:
            data = response.json()
            new_ip = data.get('new_ip', data.get('ip', '?'))
            logger.info(f"[PROXY] ✅ Новый IP: {new_ip}")
        except:
            logger.info(f"[PROXY] Ответ: {response.text[:100]}")

        # Пауза 8 сек — прокси нужно время на переключение
        time.sleep(8)
    except Exception as e:
        logger.error(f"[PROXY] Ошибка смены IP: {e}")


# =============================================================
# ЭНДПОИНТЫ
# =============================================================

TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self'
CORE_BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance'
CPA_BALANCE_URL = 'https://api.avito.ru/cpa/v3/balanceInfo'
GET_BIDS_URL_TPL = 'https://api.avito.ru/cpxpromo/1/getBids/{item_id}'
SET_MANUAL_BID_URL = 'https://api.avito.ru/cpxpromo/1/setManual'
ITEM_INFO_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/items/{item_id}/'


# =============================================================
# ТОКЕН
# =============================================================

def get_avito_access_token(client_id: str, client_secret: str) -> Union[str, None]:
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials',
    }
    try:
        logger.info(f"[TOKEN] Запрос для client_id: {client_id[:8]}...")
        response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token:
            logger.info("[TOKEN] Успех")
            return access_token
        logger.error(f"[TOKEN] access_token не найден: {token_data}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[TOKEN] Ошибка: {e}")
        return None


# =============================================================
# USER ID
# =============================================================

def get_avito_user_id(access_token: str) -> Union[int, None]:
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get(USER_INFO_URL, headers=headers, timeout=10)
        response.raise_for_status()
        user_id = response.json().get('id')
        if user_id:
            logger.info(f"[USER] ID: {user_id}")
            return user_id
        return None
    except Exception as e:
        logger.error(f"[USER] Ошибка: {e}")
        return None


# =============================================================
# БАЛАНС
# =============================================================

def get_balances(access_token: str, user_id: int) -> Dict:
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
        logger.warning(f"[BALANCE] Ошибка CPA: {e}")

    return result


# =============================================================
# ИНФОРМАЦИЯ ОБ ОБЪЯВЛЕНИИ (ЧЕРЕЗ API — БЕЗ ПАРСИНГА!)
# =============================================================

def get_item_info(access_token: str, item_id: int) -> Union[Dict, None]:
    """
    Получает title и image ТОЛЬКО через Avito API.
    НЕ парсит HTML — не нужны прокси, не бывает 429.
    """
    try:
        user_id = get_avito_user_id(access_token)
        if not user_id:
            return None

        api_url = ITEM_INFO_URL_TPL.format(user_id=user_id, item_id=item_id)
        headers = {'Authorization': f'Bearer {access_token}'}
        resp = requests.get(api_url, headers=headers, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            title = data.get('title', '')
            status = data.get('status', 'unknown')
            ad_url = data.get('url', '')

            # Картинка из API
            image_url = None
            images = data.get('images', [])
            if images:
                if isinstance(images[0], str):
                    image_url = images[0]
                elif isinstance(images[0], dict):
                    image_url = images[0].get('640x480') or images[0].get('default')

            logger.info(f"[ITEM_INFO] ✅ {item_id}: «{title}» (API)")
            return {
                "title": title,
                "image_url": image_url,
                "status": status,
                "url": ad_url,
            }
        else:
            logger.warning(f"[ITEM_INFO] API статус {resp.status_code} для {item_id}")
            return None

    except Exception as e:
        logger.error(f"[ITEM_INFO] Ошибка: {e}")
        return None


# =============================================================
# СПИСОК ОБЪЯВЛЕНИЙ
# =============================================================

def get_user_ads(access_token: str) -> Union[List[Dict], None]:
    user_id = get_avito_user_id(access_token)
    if not user_id:
        return None

    url = f"https://api.avito.ru/core/v1/accounts/{user_id}/ads/"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    try:
        logger.info(f"[ADS] Запрос объявлений {user_id}...")
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        ads = data.get('resources', [])

        formatted = []
        for ad in ads:
            if ad.get('status') == 'active':
                formatted.append({
                    'id': ad.get('id'),
                    'title': ad.get('title', 'Без названия'),
                })

        logger.info(f"[ADS] {len(formatted)} активных")
        return formatted

    except requests.exceptions.RequestException as e:
        logger.error(f"[ADS] Ошибка: {e}")
        return None


# =============================================================
# СТАВКИ
# =============================================================

def get_current_ad_price(ad_id: int, access_token: str) -> Union[float, None]:
    if not access_token:
        return None

    headers = {'Authorization': f'Bearer {access_token}'}
    url = GET_BIDS_URL_TPL.format(item_id=ad_id)

    for attempt in range(2):
        try:
            logger.info(f"[STAVKA] Попытка {attempt+1}/2")
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            bid = data.get('manual', {}).get('bidPenny')
            if bid is not None:
                price = float(bid) / 100
                logger.info(f"[STAVKA] Цена: {price} ₽")
                return price

            logger.warning("[STAVKA] bidPenny не найден")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"[STAVKA] Ошибка: {e}")
            time.sleep(3)

    return None


def set_ad_price(ad_id: int, new_price: float, access_token: str,
                 daily_limit_rub: float = None) -> bool:
    if not access_token:
        return False

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    body = {
        "itemID": ad_id,
        "actionTypeID": 5,
        "bidPenny": int(new_price * 100),
    }

    log_msg = f"Ставка {new_price} ₽"

    if daily_limit_rub and daily_limit_rub > 0:
        body["dailyBudgetPenny"] = int(daily_limit_rub * 100)
        log_msg += f" + лимит {daily_limit_rub} ₽"

    try:
        logger.info(f"[SET] {log_msg}")
        response = requests.post(
            SET_MANUAL_BID_URL, headers=headers, json=body, timeout=15
        )
        response.raise_for_status()
        logger.info(f"[SET] ✅ Успех")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"[SET] Ошибка: {e}")
        return False