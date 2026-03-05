# main_app/avito_api.py

import requests
import logging
import random
import time
from typing import Union, Dict, List

logger = logging.getLogger(__name__)

# =============================================================
# ROTATING ПРОКСИ — один на всё, IP меняется автоматически
# =============================================================

# avito_api.py — меняем только эту строку

ROTATING_PROXY = {
    'http':  'socks5h://u3822a4cd582005d0-zone-cis-region-ru:u3822a4cd582005d0@eu.proxy.rucaptcha.com:2333',
    'https': 'socks5h://u3822a4cd582005d0-zone-cis-region-ru:u3822a4cd582005d0@eu.proxy.rucaptcha.com:2333',
}

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
# ИНФОРМАЦИЯ ОБ ОБЪЯВЛЕНИИ
# =============================================================

def get_item_info(access_token: str, item_id: int) -> Union[Dict, None]:
    """
    Получает title и image через парсинг страницы объявления.
    Использует ROTATING_PROXY.
    """
    import re
    from bs4 import BeautifulSoup

    ad_url = None
    ad_status = ""

    # --- URL через API ---
    try:
        user_id = get_avito_user_id(access_token)
        if user_id:
            api_url = ITEM_INFO_URL_TPL.format(user_id=user_id, item_id=item_id)
            headers_api = {'Authorization': f'Bearer {access_token}'}
            resp_api = requests.get(api_url, headers=headers_api, timeout=15)
            if resp_api.status_code == 200:
                api_data = resp_api.json()
                ad_url = api_data.get("url")
                ad_status = api_data.get("status", "")
    except Exception as e:
        logger.warning(f"[ITEM_INFO] API не отдал URL: {e}")

    if not ad_url:
        ad_url = f"https://www.avito.ru/{item_id}"

    headers_browser = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept-Language': 'ru-RU,ru;q=0.9',
    }

    # Попытка 1: с ROTATING_PROXY, Попытка 2: без прокси
    attempts = [
        ("proxy", ROTATING_PROXY),
        ("direct", None),
    ]

    for attempt_name, attempt_proxies in attempts:
        try:
            logger.info(f"[ITEM_INFO] Парсинг {ad_url} ({attempt_name})...")
            resp_page = requests.get(
                ad_url,
                headers=headers_browser,
                proxies=attempt_proxies,
                timeout=20,
                allow_redirects=True
            )

            if resp_page.status_code != 200:
                logger.warning(f"[ITEM_INFO] {attempt_name}: статус {resp_page.status_code}")
                continue

            soup = BeautifulSoup(resp_page.text, 'html.parser')

            # --- Title ---
            title = ""
            h1 = soup.find('h1')
            if h1:
                title = h1.text.strip()
            else:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    raw = og_title.get('content', '')
                    title = raw.split(' в ')[0] if ' в ' in raw else raw.split(' | ')[0]

            # --- Image ---
            image_url = None
            hd_images = re.findall(
                r'https://\d+\.img\.avito\.st/image/\d+/[^"\'>\s]+',
                resp_page.text
            )
            if hd_images:
                image_url = hd_images[0]

            if not image_url:
                og_image = soup.find('meta', property='og:image')
                if og_image:
                    image_url = og_image.get('content')

            if not title and not image_url:
                logger.warning(f"[ITEM_INFO] {attempt_name}: пустой результат, пробуем дальше")
                continue

            logger.info(f"[ITEM_INFO] ✅ {item_id}: «{title}» ({attempt_name})")
            return {
                "title": title,
                "image_url": image_url,
                "status": ad_status or "unknown",
                "url": str(resp_page.url),
            }

        except requests.exceptions.RequestException as e:
            logger.warning(f"[ITEM_INFO] {attempt_name} ошибка: {e}")
            continue

    logger.error(f"[ITEM_INFO] ❌ {item_id}: все попытки провалились")
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

    if daily_limit_rub and daily_limit_rub > 0:
        body["dailyBudgetPenny"] = int(daily_limit_rub * 100)

    try:
        logger.info(f"[SET] Ставка {new_price} ₽ | тело: {body}")
        response = requests.post(
            SET_MANUAL_BID_URL, headers=headers, json=body, timeout=15
        )
        # ДОБАВИЛИ — показываем что вернул Avito
        logger.info(f"[SET] Ответ Avito: {response.status_code} | {response.text[:300]}")
        response.raise_for_status()
        logger.info(f"[SET] ✅ Успех")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"[SET] Ошибка: {e}")
        return False