# main_app/captcha_solver.py

import os
import requests
import time
import logging

logger = logging.getLogger(__name__)

RUCAPTCHA_API_KEY = os.environ.get('RUCAPTCHA_API_KEY', '')
GEETEST_CAPTCHA_ID = '2d9c743cf7d63dbc9db578a608196bcd'

# Мобильный прокси
MOBILE_PROXY = {
    'http':  'http://L5ldPoa7:5J3QK9a6@89.149.100.92:8031',
    'https': 'http://L5ldPoa7:5J3QK9a6@89.149.100.92:8031',
}
CHANGEIP_URL = 'http://89.149.100.92:7002/apix/reset_ip_secure?hash=aes_ecb:3cfcbdb67d4f115174c87aa6dbcf2e6b4526f06a267d8980f52a2d7a634fc8c1'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

# Глобальный кеш сессии
_cached_session = None
_session_created_at = 0
SESSION_TTL = 60 * 8  # 8 минут


def change_ip():
    """Меняем IP мобильного прокси"""
    try:
        r = requests.get(CHANGEIP_URL, timeout=10)
        logger.info(f"[PROXY] Смена IP: {r.status_code} {r.text[:100]}")
        time.sleep(3)  # ждём пока IP сменится
        return True
    except Exception as e:
        logger.error(f"[PROXY] Ошибка смены IP: {e}")
        return False


def _wait_for_result(task_id):
    for attempt in range(24):
        time.sleep(5)
        result = requests.post('https://api.rucaptcha.com/getTaskResult', json={
            'clientKey': RUCAPTCHA_API_KEY,
            'taskId': task_id
        }, timeout=30).json()

        if result.get('status') == 'ready':
            return result
        if result.get('errorId') != 0:
            logger.error(f"[CAPTCHA] Ошибка rucaptcha: {result}")
            return None
        logger.debug(f"[CAPTCHA] Ждём... попытка {attempt + 1}")

    logger.error("[CAPTCHA] Таймаут")
    return None


def _solve_geetest():
    r = requests.post('https://api.rucaptcha.com/createTask', json={
        'clientKey': RUCAPTCHA_API_KEY,
        'task': {
            'type': 'GeeTestTaskProxyless',
            'websiteURL': 'https://www.avito.ru',
            'version': 4,
            'initParameters': {'captcha_id': GEETEST_CAPTCHA_ID}
        }
    }, timeout=30).json()

    if r.get('errorId') != 0:
        logger.error(f"[CAPTCHA] Ошибка GeeTest задачи: {r}")
        return None

    logger.info(f"[CAPTCHA] GeeTest задача: {r['taskId']}")
    result = _wait_for_result(r['taskId'])
    return result['solution'] if result else None


def _solve_image(image_base64):
    r = requests.post('https://api.rucaptcha.com/createTask', json={
        'clientKey': RUCAPTCHA_API_KEY,
        'task': {
            'type': 'ImageToTextTask',
            'body': image_base64,
            'case': False,
        }
    }, timeout=30).json()

    if r.get('errorId') != 0:
        logger.error(f"[CAPTCHA] Ошибка Image задачи: {r}")
        return None

    result = _wait_for_result(r['taskId'])
    return result['solution']['text'] if result else None


def _create_new_session():
    """Создаёт новую сессию через мобильный прокси с обходом капчи."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.proxies.update(MOBILE_PROXY)

    try:
        r1 = session.get('https://www.avito.ru/moskva', timeout=15)
        logger.info(f"[CAPTCHA] Первый запрос: {r1.status_code} ({len(r1.content)} байт)")
    except Exception as e:
        logger.error(f"[CAPTCHA] Ошибка первого запроса: {e}")
        return None

    if 'Доступ ограничен' not in r1.text and r1.status_code == 200:
        logger.info("[CAPTCHA] Капчи нет — сессия готова")
        return session

    # Капча есть — решаем
    try:
        r2 = session.post(
            'https://www.avito.ru/web/1/firewallCaptcha/get',
            headers={'Content-Type': 'application/json'},
            json={'refreshAvitoCaptcha': False},
            timeout=15
        ).json()
    except Exception as e:
        logger.error(f"[CAPTCHA] Ошибка получения капчи: {e}")
        return None

    captcha_type = r2.get('result', {}).get('captcha', {}).get('type')
    logger.info(f"[CAPTCHA] Тип: {captcha_type}")

    if captcha_type != 'geeTest':
        logger.error(f"[CAPTCHA] Неизвестный тип: {captcha_type}")
        return None

    solution = _solve_geetest()
    if not solution:
        return None

    try:
        r3 = session.post(
            'https://www.avito.ru/web/1/firewallCaptcha/verify',
            headers={'Content-Type': 'application/json'},
            json={
                'captcha': '',
                'hCaptchaResponse': '',
                'lot_number': solution['lot_number'],
                'pass_token': solution['pass_token'],
                'gen_time': solution['gen_time'],
                'captcha_output': solution['captcha_output'],
            },
            timeout=15
        ).json()
    except Exception as e:
        logger.error(f"[CAPTCHA] Ошибка verify: {e}")
        return None

    logger.info(f"[CAPTCHA] GeeTest verify: {r3}")

    next_captcha = r3.get('result', {}).get('captcha', {})
    if next_captcha.get('type') == 'avitoCaptcha':
        image_b64 = next_captcha['image'].replace('data:image/png;base64,', '')
        code = _solve_image(image_b64)
        if not code:
            return None

        try:
            r4 = session.post(
                'https://www.avito.ru/web/1/firewallCaptcha/verify',
                headers={'Content-Type': 'application/json'},
                json={'captcha': code, 'hCaptchaResponse': ''},
                timeout=15
            ).json()
        except Exception as e:
            logger.error(f"[CAPTCHA] Ошибка verify image: {e}")
            return None

        if not r4.get('result', {}).get('verified'):
            logger.error("[CAPTCHA] Картинка не принята!")
            return None

    time.sleep(2)
    logger.info("[CAPTCHA] ✅ Сессия готова!")
    return session


def get_avito_session(proxies=None):
    """
    Возвращает кешированную сессию через мобильный прокси.
    proxies оставлен для совместимости но не используется — всегда MOBILE_PROXY.
    """
    global _cached_session, _session_created_at

    now = time.time()
    age = now - _session_created_at

    if _cached_session is not None and age < SESSION_TTL:
        logger.info(f"[CAPTCHA] Кешированная сессия (возраст {int(age)}с)")
        return _cached_session

    logger.info("[CAPTCHA] Создаём новую сессию...")
    session = _create_new_session()
    if session:
        _cached_session = session
        _session_created_at = now

    return session


def handle_429():
    """
    Вызывать при 429 — меняем IP и пересоздаём сессию.
    """
    global _cached_session, _session_created_at

    logger.warning("[PROXY] 429 — меняем IP...")
    change_ip()

    # Сбрасываем сессию — создастся новая с новым IP
    _cached_session = None
    _session_created_at = 0

    return get_avito_session()