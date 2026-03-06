# main_app/captcha_solver.py

import requests
import time
import logging

logger = logging.getLogger(__name__)

RUCAPTCHA_API_KEY = '091d9b4547dbfa1f90fa86e4d8d92563'
GEETEST_CAPTCHA_ID = '2d9c743cf7d63dbc9db578a608196bcd'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Accept-Encoding': 'gzip, deflate',  # сжатие — меньше трафика
}

# Глобальный кеш сессии
_cached_session = None
_session_created_at = 0
SESSION_TTL = 60 * 30  # 30 минут — потом пересоздаём


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


def _create_new_session(proxies=None):
    """Создаёт новую сессию с обходом капчи."""
    session = requests.Session()
    session.headers.update(HEADERS)
    if proxies:
        session.proxies.update(proxies)

    r1 = session.get('https://www.avito.ru/moskva', timeout=15)
    logger.info(f"[CAPTCHA] Первый запрос: {r1.status_code} ({len(r1.content)} байт)")

    if 'Доступ ограничен' not in r1.text and r1.status_code == 200:
        logger.info("[CAPTCHA] Капчи нет — сессия готова")
        return session

    r2 = session.post(
        'https://www.avito.ru/web/1/firewallCaptcha/get',
        headers={'Content-Type': 'application/json'},
        json={'refreshAvitoCaptcha': False},
        timeout=15
    ).json()

    captcha_type = r2.get('result', {}).get('captcha', {}).get('type')
    logger.info(f"[CAPTCHA] Тип: {captcha_type}")

    if captcha_type != 'geeTest':
        logger.error(f"[CAPTCHA] Неизвестный тип: {captcha_type}")
        return None

    solution = _solve_geetest()
    if not solution:
        return None

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

    logger.info(f"[CAPTCHA] GeeTest verify: {r3}")

    next_captcha = r3.get('result', {}).get('captcha', {})
    if next_captcha.get('type') == 'avitoCaptcha':
        image_b64 = next_captcha['image'].replace('data:image/png;base64,', '')
        code = _solve_image(image_b64)
        if not code:
            return None

        r4 = session.post(
            'https://www.avito.ru/web/1/firewallCaptcha/verify',
            headers={'Content-Type': 'application/json'},
            json={'captcha': code, 'hCaptchaResponse': ''},
            timeout=15
        ).json()

        if not r4.get('result', {}).get('verified'):
            logger.error("[CAPTCHA] Картинка не принята!")
            return None

    time.sleep(2)
    logger.info("[CAPTCHA] ✅ Сессия готова!")
    return session


def get_avito_session(proxies=None):
    """
    Возвращает кешированную сессию.
    Пересоздаёт только если истёк TTL или сессия сломана.
    """
    global _cached_session, _session_created_at

    now = time.time()
    age = now - _session_created_at

    # Если сессия свежая — отдаём её
    if _cached_session is not None and age < SESSION_TTL:
        logger.info(f"[CAPTCHA] Используем кешированную сессию (возраст {int(age)}с)")
        return _cached_session

    # Создаём новую
    logger.info("[CAPTCHA] Создаём новую сессию...")
    session = _create_new_session(proxies=proxies)
    if session:
        _cached_session = session
        _session_created_at = now

    return session