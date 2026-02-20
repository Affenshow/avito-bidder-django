# main_app/models.py - ИЗМЕНЕНИЯ ДЛЯ ЭТАПА 3 (НЕСКОЛЬКО АККАУНТОВ)

from django.db import models
from django.contrib.auth.models import User
from encrypted_model_fields.fields import EncryptedCharField
from django.db.models.signals import post_save
from django.dispatch import receiver
from celery import current_app
import random

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ ШАГ 1: НОВАЯ МОДЕЛЬ ДЛЯ АККАУНТОВ AVITO +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
class AvitoAccount(models.Model):
    """
    Модель для хранения данных одного аккаунта Avito.
    Привязана к основному пользователю Django.
    """
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name="avito_accounts",
        verbose_name="Владелец"
    )
    name = models.CharField(
        max_length=100, 
        verbose_name="Название аккаунта",
        help_text="Дайте понятное имя (например, 'Рабочий', 'Личный')"
    )
    avito_client_id = EncryptedCharField(max_length=255, verbose_name="Avito Client ID")
    avito_client_secret = EncryptedCharField(max_length=255, verbose_name="Avito Client Secret")
    
    def __str__(self):
        return f"Аккаунт Avito '{self.name}' ({self.user.username})"

    class Meta:
        verbose_name = "Аккаунт Avito"
        verbose_name_plural = "Аккаунты Avito"
        unique_together = ('user', 'name')


# --- МОДЕЛЬ ЗАДАЧИ ---
class BiddingTask(models.Model):
    # +++ ИЗМЕНЕНИЕ 1: Добавляем привязку к AvitoAccount +++
    avito_account = models.ForeignKey(
        AvitoAccount, 
        on_delete=models.CASCADE, 
        related_name='tasks',
        verbose_name="Аккаунт Avito",
        # Мы разрешим этому полю быть пустым на время переходного периода
        null=True, 
        blank=True 
    )
    
    # +++ ИЗМЕНЕНИЕ 2: Разрешаем полю user быть пустым (для новых задач оно не нужно) +++
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        verbose_name="Пользователь",
        null=True, 
        blank=True
    )

    ad_id = models.BigIntegerField(verbose_name="ID объявления Avito")
    title = models.CharField(max_length=255, blank=True, verbose_name="Название объявления")
    image_url = models.URLField(max_length=1000, blank=True, null=True, verbose_name="URL картинки")
    search_url = models.URLField(max_length=1000, verbose_name="URL для поиска")
    min_price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00, verbose_name="Мин. цена")
    max_price = models.DecimalField(max_digits=10, decimal_places=2, default=50.00, verbose_name="Макс. цена")
    bid_step = models.DecimalField(max_digits=10, decimal_places=2, default=1.00, verbose_name="Шаг ставки (₽)")
    target_position_min = models.PositiveIntegerField(default=1, verbose_name="Целевая позиция (от)")
    target_position_max = models.PositiveIntegerField(default=10, verbose_name="Целевая позиция (до)")
    daily_budget = models.DecimalField(
    max_digits=10,
    decimal_places=2,
    default=0.00,
    verbose_name="Дневный лимит трат (₽, 0 = без лимита)"
)
    
    freeze_price_if_not_found = models.BooleanField(
        default=False,
        verbose_name="Выход из 50-го места",
        help_text="В случае если объявление окажется ниже 50-го места, то стоимость просмотра НЕ БУДЕТ ПОВЫШАТЬСЯ (чаще всего актуально в нишах, где используется массовый постинг)"
    )
    
    current_position = models.PositiveIntegerField(null=True, blank=True, verbose_name="Текущая позиция в выдаче")
    current_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Текущая ставка")
    schedule = models.TextField(default="[]", blank=True, verbose_name="Расписание (JSON-строка)")
    
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    last_run = models.DateTimeField(
    null=True, blank=True,
    verbose_name="Последний запуск"
)

    def __str__(self):
        return f"Задание #{self.id} для объявления {self.ad_id}"

    class Meta:
        verbose_name = "Задание для биддера"
        verbose_name_plural = "Задания для биддера"
        ordering = ['-created_at']


# --- МОДЕЛЬ ПРОФИЛЯ ---
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # +++ ИЗМЕНЕНИЕ 3: Ключи Avito отсюда УБРАНЫ. Они переехали в AvitoAccount +++
    # avito_client_id = EncryptedCharField(...)
    # avito_client_secret = EncryptedCharField(...)
    
    def __str__(self):
        return f"Профиль для {self.user.username}"
    
    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"


# --- МОДЕЛЬ ЛОГОВ (без изменений) ---
class TaskLog(models.Model):
    # ... (код без изменений) ...
    task = models.ForeignKey(BiddingTask, on_delete=models.CASCADE, related_name='logs')
    timestamp = models.DateTimeField(auto_now_add=True)
    LEVEL_CHOICES = [('INFO', 'Информация'), ('WARNING', 'Предупреждение'), ('ERROR', 'Ошибка')]
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    message = models.TextField(verbose_name="Сообщение лога")
    def __str__(self):
        return f"Лог для задачи #{self.task.id} от {self.timestamp.strftime('%Y-%m-%d %H:%M')}"
    class Meta:
        verbose_name = "Запись лога"
        verbose_name_plural = "Записи логов"
        ordering = ['-timestamp']


# --- СИГНАЛЫ (без изменений) ---
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=BiddingTask)
def auto_start_bidding(sender, instance, created, **kwargs):
    if created and instance.is_active:  # только при создании
        delay = random.randint(180, 480)  # 3–8 минут
        current_app.send_task('main_app.tasks.run_bidding_for_task', args=[instance.id], countdown=delay)