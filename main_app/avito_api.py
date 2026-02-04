# main_app/avito_api.py

import requests
import logging
from typing import Union, Dict

logger = logging.getLogger(__name__)

CHANGE_IP_URL = 'https://changeip.mobileproxy.space/?proxy_key=65a15a75eb565bba6e220d15559005e3'

# --- ENDPOINT'Ы ---
TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self'
CORE_BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance'
CPA_BALANCE_URL = 'https://api.avito.ru/cpa/v3/balanceInfo'

# --- Актуальные эндпоинты для работы со ставками! ---
GET_BIDS_URL_TPL = 'https://api.avito.ru/cpxpromo/1/getBids/{item_id}'  # инфа по диапазонам, min/max ставке, etc
SET_MANUAL_BID_URL = 'https://api.avito.ru/cpxpromo/1/setManual'         # РУЧНАЯ установка ставки

def get_avito_access_token(client_id: str, client_secret: str) -> Union[str, None]:
    """Обменивает client_id и client_secret на access_token."""
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    data = {'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'client_credentials'}
    try:
        # logger.info(f"[API] Запрос access_token для client_id: {client_id}")
        response = requests.post(TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token:
            return access_token
        logger.error(f"[API] access_token не найден в ответе: {token_data}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[API] Ошибка токена: {e}")
        if hasattr(e, 'response') and e.response: logger.error(f"Ответ: {e.response.text}")
        return None

def get_avito_user_id(access_token: str) -> Union[int, None]:
    """Получает ID текущего пользователя."""
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get(USER_INFO_URL, headers=headers)
        response.raise_for_status()
        return response.json().get('id')
    except Exception as e:
        logger.error(f"[API] Ошибка user_id: {e}")
        return None

def get_balances(access_token: str, user_id: int) -> Dict:
    """
    Получить баланс кошелька и аванс (CPA).
    """
    result = {'real': None, 'bonus': None}
    headers = {'Authorization': f'Bearer {access_token}'}

    # Core balance
    try:
        resp = requests.get(CORE_BALANCE_URL_TPL.format(user_id=user_id), headers=headers)
        resp.raise_for_status()
        data = resp.json()
        result['real'] = data.get('real', 0)
    except Exception as e:
        logger.warning(f"[Avito API] Ошибка реального баланса: {e}")

    # CPA balance
    try:
        cpa_headers = {**headers, 'X-Source': 'AvitoBidder'}
        resp = requests.post(CPA_BALANCE_URL, headers=cpa_headers, json={})
        resp.raise_for_status()
        data = resp.json()
        result['bonus'] = data.get('balance', 0) / 100
    except Exception as e:
        logger.warning(f"[Avito API] Ошибка CPA-аванса: {e}")
    return result

def get_current_ad_price(ad_id: int, access_token: str) -> Union[float, None]:
    """
    Получает текущую ставку для объявления, используя ПРАВИЛЬНЫЙ эндпоинт.
    """
    if not access_token: return None
    headers = {'Authorization': f'Bearer {access_token}'}
    # --- ИСПОЛЬЗУЕМ ПРАВИЛЬНЫЙ URL ИЗ ВАШЕЙ ДОКУМЕНТАЦИИ ---
    url = GET_BIDS_URL_TPL.format(item_id=ad_id)

    try:
        logger.info(f"--- [CPxPromo API] Запрос GET к {url}...")
        # --- МЕТОД GET, КАК В ДОКУМЕНТАЦИИ ---
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        print(f"--- [CPxPromo API] ПОЛНЫЙ ОТВЕТ ПО СТАВКАМ: {data} ---")

        bid_in_kopecks = data.get('manual', {}).get('bidPenny')
        if bid_in_kopecks is not None:
            price = float(bid_in_kopecks) / 100
            logger.info(f"--- [CPxPromo API] Найдена текущая цена: {price} RUB ---")
            return price
        logger.error(f"--- [CPxPromo API] Поле 'manual.bidPenny' не найдено в ответе!")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"--- [CPxPromo API] Ошибка при получении ставок: {e}")
        if e.response: print(f"--- [CPxPromo API] Ответ сервера: {e.response.text}")
        return None



def set_ad_price(ad_id: int, new_price: float, access_token: str) -> bool:
    """
    Устанавливает новую цену через эндпоинт /setManual.
    """
    if not access_token:
        return False

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Мы выяснили, что actionTypeID=5 - правильный для наших объявлений
    ACTION_TYPE_ID_FOR_VIEWS = 5

    body = {
        "itemID": ad_id,
        "actionTypeID": ACTION_TYPE_ID_FOR_VIEWS,
        "bidPenny": int(new_price * 100)  # Цена в копейках
    }

    try:
        logger.info(f"--- [CPxPromo API] Устанавливаем цену: {body}")
        
        # Используем правильный URL для setManual
        response = requests.post(SET_MANUAL_BID_URL, headers=headers, json=body)
        
        # Просто проверяем, что статус ответа успешный (2xx).
        # Не пытаемся читать .json(), так как ответ пустой.
        response.raise_for_status()
        
        logger.info(f"--- [CPxPromo API] УСПЕХ! Цена для ad_id={ad_id} изменена. (Статус: {response.status_code})")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"--- [CPxPromo API] ОШИБКА при установке цены: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"--- [CPxPromo API] Ответ сервера: {e.response.text}")
        return False


def rotate_proxy_ip():
    """Отправляет запрос на смену IP-адреса прокси."""
    try:
        logger.info("--- [PROXY] Отправка запроса на смену IP...")
        response = requests.get(CHANGE_IP_URL, timeout=30)
        response.raise_for_status()
        logger.info(f"--- [PROXY] IP успешно сменен! Ответ: {response.text}")
        return True
    except Exception as e:
        logger.error(f"--- [PROXY] Ошибка при смене IP: {e}")
        return False