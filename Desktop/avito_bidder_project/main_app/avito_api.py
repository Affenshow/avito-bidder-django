import requests
import logging
from typing import Union, Dict

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://api.avito.ru/token/'
USER_INFO_URL = 'https://api.avito.ru/core/v1/accounts/self/'
BALANCE_URL_TPL = 'https://api.avito.ru/core/v1/accounts/{user_id}/balance/'

def get_avito_access_token(client_id: str, client_secret: str) -> Union[str, None]:
    """Обменивает client_id и client_secret на временный access_token."""
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://b2b.avito.ru/'
    }
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials'
    }
    try:
        logger.info(f"--- Запрос токена с Referer: {headers['Referer']} ---")
        response = requests.post(TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token:
            return access_token
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении токена: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Ответ сервера: {e.response.text}")
        return None

def get_avito_account_id(access_token: str) -> Union[int, None]:
    """Получает account_id текущего аккаунта через API."""
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        response = requests.get(USER_INFO_URL, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get('id')  # Обычно это account_id
    except Exception as e:
        logger.error(f"Ошибка при получении account_id: {e}")
        return None

def get_avito_balance(access_token: str, account_id: int) -> Union[Dict, None]:
    """Получает баланс аккаунта по account_id через API."""
    if not access_token or not account_id:
        return None
    headers = {'Authorization': f'Bearer {access_token}'}
    balance_url = BALANCE_URL_TPL.format(user_id=account_id)
    try:
        response = requests.get(balance_url, headers=headers)
        response.raise_for_status()
        balance_data = response.json()

        print(f"Полный ответ API баланса: {balance_data}")

        return balance_data  # Возвращаем полный словарь с балансом для анализа
    except Exception as e:
        logger.error(f"Ошибка при получении баланса: {e}")
        return None
