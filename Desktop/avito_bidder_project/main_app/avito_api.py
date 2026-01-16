# main_app/avito_api.py
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
        # --- ИМИТИРУЕМ ЗАПРОС С "ДОВЕРЕННОГО" ИСТОЧНИКА ---
        'Referer': 'https://b2b.avito.ru/'
    }
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials'
    }
    try:
        logger.info(f"--- [API-HACK] Запрос токена с Referer: {headers['Referer']} ---")
        response = requests.post(TOKEN_URL, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        if access_token: return access_token
        return None
    except Exception as e:
        logger.error(f"--- [API-HACK] Ошибка при получении токена: {e}")
        if hasattr(e, 'response') and e.response: logger.error(f"--- [API-HACK] Ответ сервера: {e.response.text}")
        return None

def get_avito_balance(access_token: str, profile_id: int) -> Union[Dict, None]: # <-- Принимаем profile_id
    """
    ГИПОТЕЗА: Используем profile_id вместо user_id для запроса баланса.
    """
    if not access_token or not profile_id:
        return None

    headers = {'Authorization': f'Bearer {access_token}'}

    try:
        # --- ГЛАВНЫЙ ЭКСПЕРИМЕНТ ---
        # Формируем URL, подставляя ID ПРОФИЛЯ вместо ID пользователя
        balance_url = BALANCE_URL_TPL.format(user_id=profile_id)
        
        logger.info(f"--- [ЭКСПЕРИМЕНТ] Запрос баланса по URL: {balance_url} ---")
        balance_response = requests.get(balance_url, headers=headers)
        balance_response.raise_for_status()
        balance_data = balance_response.json()
        
        print(f"--- [ЭКСПЕРИМЕНТ] ПОЛНЫЙ ОТВЕТ API БАЛАНСА: {balance_data} ---")

        return {
            'real': balance_data.get('real', 0.0),
            'bonus': balance_data.get('bonus', 0.0)
        }
    except Exception as e:
        logger.error(f"--- [ЭКСПЕРИМЕНТ] Ошибка при получении баланса: {e}")
        if hasattr(e, 'response') and e.response: logger.error(f"--- [ЭКСПЕРИМЕНТ] Ответ сервера: {e.response.text}")
        return None