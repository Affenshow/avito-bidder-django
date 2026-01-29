# debug_path.py
import sys
import os

print("--- ПРОВЕРКА ПУТЕЙ ПОИСКА МОДУЛЕЙ (sys.path) ---")

# Получаем текущую рабочую директорию
current_working_directory = os.getcwd()
print(f"\nТекущая рабочая директория (os.getcwd()):\n{current_working_directory}\n")

print("Содержимое sys.path:")
# Печатаем каждый путь на новой строке для удобства
for path in sys.path:
    print(path)

print("\n--- ПРОВЕРКА ЗАВЕРШЕНА ---")
