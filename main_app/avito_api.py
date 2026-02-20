# main_app/avito_api.py

import requests
import logging
import random
import time
from typing import Union, Dict, List, Tuple

logger = logging.getLogger(__name__)

# --- Прокси-пул ---
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

# --- Отслеживание последней смены IP (защита от "Already change IP") ---
_last_rotate_time = {}  # {port: timestamp}
_ROTATE_COOLDOWN = 30   # было 60


def get_random_proxy() -> Tuple[Dict, Dict]:
    """Возвращает случайный прокси из пула."""
    proxy = random.choice(PROXY_POOL)
    return {
        'http': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
        'https': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
    }, proxy


def get_other_proxy(exclude_port: int) -> Tuple[Dict, Dict]:
    """Возвращает прокси с ДРУГИМ портом (если есть)."""
    others = [p for p in PROXY_POOL if p['port'] != exclude_port]
    if not others:
        return get_random_proxy()
    proxy = random.choice(others)
    return {
        'http': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
        'https': f'http://{proxy["user"]}:{proxy["pass"]}@{proxy["host"]}:{proxy["port"]}',
    }, proxy


def rotate_proxy_ip(proxy: Dict) -> bool:
    """
    Смена IP с защитой от слишком частых вызовов.
    Возвращает True если IP сменён, False если пропущено.
    """
    port = proxy['port']
    now = time.time()

    # Проверяем cooldown
    last_time = _last_rotate_time.get(port, 0)
    elapsed = now - last_time
    if elapsed < _ROTATE_COOLDOWN:
        logger.info(
            f"[PROXY] Пропуск смены IP для порта {port} "
            f"(прошло {int(elapsed)} сек, нужно {_ROTATE_COOLDOWN})"
        )
        return False

    try:
        logger.info(f"[PROXY] Смена IP для порта {port}...")
        response = requests.get(proxy['change_ip_url'], timeout=15)
        _last_rotate_time[port] = time.time()

        resp_text = response.text
        # Проверяем ответ
        if 'Already change' in resp_text:
            logger.warning(
                f"[PROXY] Порт {port}: IP ещё меняется, ждём..."
            )
            time.sleep(10)
            return False

        if response.status_code == 200:
            # Извлекаем новый IP из ответа (если есть)
            if 'Новый ip-адрес' in resp_text or 'success' in resp_text.lower():
                logger.info(f"[PROXY] IP сменён для порта {port}")
            else:
                logger.info(
                    f"[PROXY] Ответ смены IP порт {port}: "
                    f"{resp_text[:100]}"
                )
            time.sleep(7)  # даём прокси время на применение
            return True

        logger.warning(
            f"[PROXY] Ошибка смены IP порт {port}: "
            f"HTTP {response.status_code}"
        )
        return False

    except Exception as e:
        logger.error(f"[PROXY] Ошибка смены IP порт {port}: {e}")
        return False


# --- Основные эндпоинты Avito ---
TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self'
CORE_BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance'
CPA_BALANCE_URL = 'https://api.avito.ru/cpa/v3/balanceInfo'
GET_BIDS_URL_TPL = 'https://api.avito.ru/cpxpromo/1/getBids/{item_id}'
SET_MANUAL_BID_URL = 'https://api.avito.ru/cpxpromo/1/setManual'


def get_avito_access_token(
    client_id: str, client_secret: str
) -> Union[str, None]:
    """Обмен client_id и client_secret на access_token."""
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/121.0.0.0 Safari/537.36'
        ),
    }
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials',
    }
    try:
        logger.info(f"[TOKEN] Запрос для client_id: {client_id[:8]}...")
        response = requests.post(
            TOKEN_URL, headers=headers, data=data, timeout=15
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token:
            logger.info("[TOKEN] Успех: токен получен")
            return access_token
        logger.error(
            f"[TOKEN] access_token не найден в ответе: {token_data}"
        )
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
        response = requests.get(
            USER_INFO_URL, headers=headers, timeout=10
        )
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
        resp = requests.post(
            CPA_BALANCE_URL, headers=cpa_headers, json={}, timeout=10
        )
        resp.raise_for_status()
        result['bonus'] = resp.json().get('balance', 0) / 100
    except Exception as e:
        logger.warning(f"[BALANCE] Ошибка CPA-аванса: {e}")

    return result


def get_current_ad_price(
    ad_id: int, access_token: str
) -> Union[float, None]:
    """Получает текущую ставку для объявления."""
    if not access_token:
        return None

    headers = {'Authorization': f'Bearer {access_token}'}
    url = GET_BIDS_URL_TPL.format(item_id=ad_id)

    max_retries = 3
    last_proxy_port = None

    for attempt in range(max_retries):
        # На повторных попытках берём ДРУГОЙ прокси
        if last_proxy_port:
            proxies, proxy_used = get_other_proxy(last_proxy_port)
        else:
            proxies, proxy_used = get_random_proxy()

        last_proxy_port = proxy_used['port']

        try:
            logger.info(
                f"[STAVKA] Попытка {attempt+1}/{max_retries} "
                f"через прокси {proxy_used['port']}"
            )
            response = requests.get(
                url, headers=headers, proxies=proxies, timeout=15
            )
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
            logger.error(
                f"[STAVKA] Ошибка попытка {attempt+1} "
                f"(прокси {proxy_used['port']}): {e}"
            )
            rotate_proxy_ip(proxy_used)
            time.sleep(5)

    logger.error("[STAVKA] Все попытки провалились")
    return None


def set_ad_price(
    ad_id: int,
    new_price: float,
    access_token: str,
    daily_limit_rub: float = None,
) -> bool:
    """
    Устанавливает новую ставку с ПОВТОРНЫМИ ПОПЫТКАМИ.
    """
    if not access_token:
        logger.error("[SET] Нет access_token — установка невозможна")
        return False

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/121.0.0.0 Safari/537.36'
        ),
    }

    ACTION_TYPE_ID_FOR_VIEWS = 5

    body = {
        "itemID": ad_id,
        "actionTypeID": ACTION_TYPE_ID_FOR_VIEWS,
        "bidPenny": int(new_price * 100),
    }

    log_message = f"Установка ставки {new_price} ₽"

    if daily_limit_rub is not None:
        if daily_limit_rub > 0:
            limit_kopecks = int(daily_limit_rub * 100)
            body["dailyBudgetPenny"] = limit_kopecks
            log_message += f" + лимита {daily_limit_rub} ₽"
        else:
            log_message += " (лимит 0 — параметр не передаётся)"

    max_retries = 3
    last_proxy_port = None

    for attempt in range(max_retries):
        # На повторных попытках — другой прокси
        if last_proxy_port:
            proxies, proxy_used = get_other_proxy(last_proxy_port)
        else:
            proxies, proxy_used = get_random_proxy()

        last_proxy_port = proxy_used['port']

        try:
            logger.info(
                f"[SET] Попытка {attempt+1}/{max_retries}: "
                f"{log_message} через прокси {proxy_used['port']}"
            )
            logger.info(f"[SET] Body: {body}")

            response = requests.post(
                SET_MANUAL_BID_URL,
                headers=headers,
                json=body,
                proxies=proxies,
                timeout=15,
            )
            response.raise_for_status()

            logger.info(
                f"[SET] УСПЕХ! Статус: {response.status_code}, "
                f"Ответ: {response.text or 'пусто'}"
            )
            return True

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[SET] Ошибка попытка {attempt+1} "
                f"(прокси {proxy_used['port']}): {e}"
            )
            if hasattr(e, 'response') and e.response is not None:
                status = e.response.status_code
                text = e.response.text or 'пусто'
                logger.error(
                    f"[SET] Статус: {status}, Ответ: {text}"
                )
                # Если Avito вернул 400/403 — повтор не поможет
                if status in (400, 403):
                    logger.warning(
                        "[SET] Avito отклонил запрос — "
                        "повтор бессмысленен"
                    )
                    return False

            rotate_proxy_ip(proxy_used)
            time.sleep(5)

    logger.error("[SET] Все попытки установки ставки провалились")
    return False


def get_user_ads(access_token: str) -> Union[List[Dict], None]:
    """Получает список активных объявлений пользователя."""
    user_id = get_avito_user_id(access_token)
    if not user_id:
        logger.error("[ADS] Не удалось получить user_id")
        return None

    url = f"https://api.avito.ru/core/v1/accounts/{user_id}/ads/"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    max_retries = 3
    last_proxy_port = None

    for attempt in range(max_retries):
        if last_proxy_port:
            proxies, proxy_used = get_other_proxy(last_proxy_port)
        else:
            proxies, proxy_used = get_random_proxy()

        last_proxy_port = proxy_used['port']

        try:
            logger.info(
                f"[ADS] Попытка {attempt+1}/{max_retries} "
                f"через прокси {proxy_used['port']}..."
            )
            response = requests.get(
                url, headers=headers, proxies=proxies, timeout=20
            )
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

            logger.info(
                f"[ADS] Найдено {len(formatted)} активных объявлений"
            )
            return formatted

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[ADS] Ошибка попытка {attempt+1} "
                f"(прокси {proxy_used['port']}): {e}"
            )
            rotate_proxy_ip(proxy_used)
            time.sleep(5)

    logger.error("[ADS] Все попытки получения объявлений провалились")
    return None

def get_ad_info_by_api(ad_id: int, access_token: str) -> Union[Dict, None]:
    """
    Получает заголовок и фото объявления напрямую через API Avito.
    Не требует парсинга страницы поиска.
    """
    if not access_token:
        return None

    user_id = get_avito_user_id(access_token)
    if not user_id:
        return None

    url = f"https://api.avito.ru/core/v1/accounts/{user_id}/items/{ad_id}/"
    headers = {'Authorization': f'Bearer {access_token}'}

    max_retries = 3
    last_proxy_port = None

    for attempt in range(max_retries):
        if last_proxy_port:
            proxies, proxy_used = get_other_proxy(last_proxy_port)
        else:
            proxies, proxy_used = get_random_proxy()

        last_proxy_port = proxy_used['port']

        try:
            logger.info(
                f"[AD_INFO] Попытка {attempt+1}/{max_retries} "
                f"для ad_id={ad_id} через прокси {proxy_used['port']}"
            )
            response = requests.get(
                url, headers=headers, proxies=proxies, timeout=15
            )
            response.raise_for_status()
            data = response.json()

            title = data.get('title', 'Без названия')

            # Фото — берём первое из списка images
            image_url = None
            images = data.get('images', [])
            if images:
                # Avito отдаёт разные размеры
                first_image = images[0]
                if isinstance(first_image, dict):
                    image_url = (
                        first_image.get('640x480')
                        or first_image.get('1280x960')
                        or first_image.get('208x156')
                        or next(iter(first_image.values()), None)
                    )
                elif isinstance(first_image, str):
                    image_url = first_image

            logger.info(
                f"[AD_INFO] ad_id={ad_id}: "
                f"title='{title[:50]}', image={'есть' if image_url else 'нет'}"
            )

            return {
                'title': title,
                'image_url': image_url,
                'price': data.get('price'),
                'status': data.get('status'),
                'url': data.get('url'),
            }

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[AD_INFO] Ошибка попытка {attempt+1} "
                f"(прокси {proxy_used['port']}): {e}"
            )
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"[AD_INFO] Ответ: {e.response.text[:200]}")
            rotate_proxy_ip(proxy_used)
            time.sleep(3)

    logger.error(f"[AD_INFO] Все попытки провалились для ad_id={ad_id}")
    return None