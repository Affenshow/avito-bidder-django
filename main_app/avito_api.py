
# main_app/avito_api.py

import requests
import logging
import time
from typing import Union, Dict, List, Optional

logger = logging.getLogger(__name__)

# =============================================================
# ЭНДПОИНТЫ
# =============================================================
TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self'
BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance'
CPA_BALANCE_URL = 'https://api.avito.ru/cpa/v3/balanceInfo'
GET_BIDS_URL_TPL = 'https://api.avito.ru/cpxpromo/1/getBids/{item_id}'
SET_MANUAL_BID_URL = 'https://api.avito.ru/cpxpromo/1/setManual'
ITEM_INFO_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/items/{item_id}/'
ITEMS_URL = 'https://api.avito.ru/core/v1/items'

# Оставляем для совместимости (tasks.py импортирует)
PROXY_POOL = []


def get_random_proxy(exclude_port=None):
    return {}, {}


def rotate_proxy_ip(proxy):
    pass


# =============================================================
# ТОКЕН
# =============================================================
def get_avito_access_token(client_id: str, client_secret: str) -> Optional[str]:
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials',
    }
    try:
        logger.info(f"[TOKEN] Запрос для client_id: {client_id[:8]}...")
        r = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)
        r.raise_for_status()
        token = r.json().get('access_token')
        if token:
            logger.info("[TOKEN] Успех")
            return token
        logger.error(f"[TOKEN] access_token не найден: {r.json()}")
        return None
    except Exception as e:
        logger.error(f"[TOKEN] Ошибка: {e}")
        return None


# =============================================================
# USER ID
# =============================================================
def get_avito_user_id(access_token: str) -> Optional[int]:
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        r = requests.get(USER_INFO_URL, headers=headers, timeout=10)
        r.raise_for_status()
        user_id = r.json().get('id')
        if user_id:
            logger.info(f"[USER] ID: {user_id}")
        return user_id
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
        url = BALANCE_URL_TPL.format(user_id=user_id)
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        result['real'] = r.json().get('real', 0)
    except Exception as e:
        logger.warning(f"[BALANCE] Ошибка реального баланса: {e}")

    try:
        r = requests.post(
            CPA_BALANCE_URL,
            headers={**headers, 'X-Source': 'AvitoBidder'},
            json={},
            timeout=10
        )
        r.raise_for_status()
        result['bonus'] = r.json().get('balance', 0) / 100
    except Exception as e:
        logger.warning(f"[BALANCE] Ошибка CPA баланса: {e}")

    return result


# =============================================================
# СТАВКИ И ПОЗИЦИЯ — ГЛАВНАЯ ФУНКЦИЯ
# =============================================================
def get_bids_table(ad_id: int, access_token: str) -> Optional[Dict]:
    """
    Возвращает таблицу ставок от Avito.
    
    Пример ответа:
    {
        'current_bid': 27.0,        # текущая ставка в рублях
        'rec_bid': 27.0,            # рекомендованная ставка
        'min_bid': 2.0,             # минимальная ставка
        'max_bid': 1614.0,          # максимальная ставка
        'bids': [
            {'price': 3.0, 'position': 13},
            {'price': 5.0, 'position': 15},
            ...
        ]
    }
    """
    headers = {'Authorization': f'Bearer {access_token}'}
    url = GET_BIDS_URL_TPL.format(item_id=ad_id)

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

        manual = data.get('manual', {})
        bids_raw = manual.get('bids', [])

        bids = [
            {
                'price': b['valuePenny'] / 100,
                'position': b['compare']
            }
            for b in bids_raw
        ]

        # Текущая ставка — ищем bidPenny в ответе
        current_bid_penny = manual.get('bidPenny')
        current_bid = current_bid_penny / 100 if current_bid_penny else None

        result = {
            'current_bid': current_bid,
            'rec_bid': manual.get('recBidPenny', 0) / 100,
            'min_bid': manual.get('minBidPenny', 0) / 100,
            'max_bid': manual.get('maxBidPenny', 0) / 100,
            'bids': bids,
        }

        logger.info(
            f"[BIDS] {ad_id}: текущая={current_bid}₽, "
            f"рек={result['rec_bid']}₽, "
            f"строк в таблице={len(bids)}"
        )
        return result

    except Exception as e:
        logger.error(f"[BIDS] Ошибка для {ad_id}: {e}")
        return None


def find_bid_for_position(bids: List[Dict], target_position: int) -> Optional[float]:
    """
    Находит минимальную ставку для достижения целевой позиции.
    
    Логика: ищем наименьшую ставку при которой позиция <= target_position
    
    Пример: хотим позицию 15
    - 3 руб → позиция 13 ✅ (13 <= 15)
    - 2 руб → позиция 0  ❌
    Возвращаем 3 руб — минимальная ставка для позиции ≤ 15
    """
    if not bids:
        return None

    # Фильтруем только строки где позиция в нужном диапазоне
    # position > 0 означает что объявление показывается
    candidates = [
        b for b in bids
        if 0 < b['position'] <= target_position
    ]

    if not candidates:
        # Целевая позиция недостижима (слишком высокая конкуренция)
        # Берём строку с наименьшей позицией (самой высокой в топе)
        visible = [b for b in bids if b['position'] > 0]
        if visible:
            best = min(visible, key=lambda x: x['position'])
            logger.warning(
                f"[BIDS] Позиция {target_position} недостижима, "
                f"лучшее: позиция {best['position']} за {best['price']}₽"
            )
            return best['price']
        return None

    # Берём минимальную ставку среди подходящих
    best = min(candidates, key=lambda x: x['price'])
    logger.info(
        f"[BIDS] Для позиции ≤{target_position}: "
        f"{best['price']}₽ → позиция {best['position']}"
    )
    return best['price']


def get_current_position_from_bids(bids: List[Dict], current_bid: float) -> Optional[int]:
    """
    Определяет текущую позицию по текущей ставке.
    Ищем строку в таблице ближайшую к текущей ставке.
    """
    if not bids or current_bid is None:
        return None

    # Ищем строку где ставка <= current_bid
    candidates = [b for b in bids if b['price'] <= current_bid and b['position'] > 0]

    if not candidates:
        return None

    # Берём строку с максимальной ставкой (ближайшую к текущей)
    closest = max(candidates, key=lambda x: x['price'])
    return closest['position']


# Оставляем для совместимости со старым кодом
def get_current_ad_price(ad_id: int, access_token: str) -> Optional[float]:
    """Совместимость: возвращает текущую ставку."""
    result = get_bids_table(ad_id, access_token)
    if result:
        return result.get('current_bid')
    return None


def set_ad_price(
    ad_id: int,
    new_price: float,
    access_token: str,
    daily_limit_rub: float = None
) -> bool:
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    body = {
        'itemID': ad_id,
        'actionTypeID': 5,
        'bidPenny': int(new_price * 100),
    }
    if daily_limit_rub and daily_limit_rub > 0:
        body['dailyBudgetPenny'] = int(daily_limit_rub * 100)

    try:
        logger.info(f"[SET] Ставка {new_price}₽ для {ad_id}")
        r = requests.post(SET_MANUAL_BID_URL, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        logger.info(f"[SET] ✅ Успех")
        return True
    except Exception as e:
        logger.error(f"[SET] Ошибка: {e}")
        return False


# =============================================================
# ИНФОРМАЦИЯ ОБ ОБЪЯВЛЕНИИ
# =============================================================
def get_item_info(access_token: str, item_id: int) -> Optional[Dict]:
    try:
        user_id = get_avito_user_id(access_token)
        if not user_id:
            return None

        url = ITEM_INFO_URL_TPL.format(user_id=user_id, item_id=item_id)
        headers = {'Authorization': f'Bearer {access_token}'}
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code == 200:
            data = r.json()
            image_url = None
            images = data.get('images', [])
            if images:
                img = images[0]
                if isinstance(img, str):
                    image_url = img
                elif isinstance(img, dict):
                    image_url = img.get('640x480') or img.get('default')

            return {
                'title': data.get('title', ''),
                'image_url': image_url,
                'status': data.get('status', 'unknown'),
                'url': data.get('url', ''),
            }
        logger.warning(f"[ITEM_INFO] Статус {r.status_code} для {item_id}")
        return None
    except Exception as e:
        logger.error(f"[ITEM_INFO] Ошибка: {e}")
        return None


# =============================================================
# СПИСОК ОБЪЯВЛЕНИЙ
# =============================================================
def get_user_ads(access_token: str) -> Optional[List[Dict]]:
    headers = {'Authorization': f'Bearer {access_token}'}
    all_ads = []
    page = 1

    try:
        while True:
            r = requests.get(
                ITEMS_URL,
                headers=headers,
                params={'per_page': 100, 'page': page, 'status': 'active'},
                timeout=20
            )
            r.raise_for_status()
            resources = r.json().get('resources', [])

            if not resources:
                break

            for ad in resources:
                all_ads.append({
                    'id': ad.get('id'),
                    'title': ad.get('title', 'Без названия'),
                    'price': ad.get('price', 0),
                    'url': ad.get('url', ''),
                    'address': ad.get('address', ''),
                    'category': ad.get('category', {}).get('name', ''),
                    'status': ad.get('status', ''),
                })

            if len(resources) < 100:
                break
            page += 1

        logger.info(f"[ADS] Итого: {len(all_ads)}")
        return all_ads
    except Exception as e:
        logger.error(f"[ADS] Ошибка: {e}")
        return None
