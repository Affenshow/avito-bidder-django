# main_app/models.py
from django.db import models
from django.contrib.auth.models import User
from encrypted_model_fields.fields import EncryptedCharField
from django.db.models.signals import post_save
from django.dispatch import receiver

# --- МОДЕЛЬ ЗАДАЧИ ---
class BiddingTask(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    ad_id = models.BigIntegerField(verbose_name="ID объявления Avito")
    search_url = models.URLField(max_length=1000, verbose_name="URL для поиска")
    min_price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00, verbose_name="Мин. цена")
    max_price = models.DecimalField(max_digits=10, decimal_places=2, default=50.00, verbose_name="Макс. цена")
    bid_step = models.DecimalField(max_digits=10, decimal_places=2, default=1.00, verbose_name="Шаг ставки (₽)")
    target_position = models.PositiveIntegerField(default=10, verbose_name="Целевая позиция (до)")
    
    # Мы будем использовать JSONField, а для SQLite Django сам создаст эмуляцию через TextField.
    # Нам не нужно делать if/else.
    schedule = models.TextField(default="[]", blank=True, verbose_name="Расписание (JSON-строка)")
    
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    def __str__(self):
        return f"Задание #{self.id} для объявления {self.ad_id}"

    class Meta:
        verbose_name = "Задание для биддера"
        verbose_name_plural = "Задания для биддера"
        ordering = ['-created_at']

# --- МОДЕЛЬ ПРОФИЛЯ ---
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avito_client_id = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="Avito Client ID")
    avito_client_secret = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="Avito Client Secret")

    def __str__(self):
        return f"Профиль для {self.user.username}"

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

# --- МОДЕЛЬ ЛОГОВ ---
class TaskLog(models.Model):
    task = models.ForeignKey(BiddingTask, on_delete=models.CASCADE, related_name='logs')
    timestamp = models.DateTimeField(auto_now_add=True)
    LEVEL_CHOICES = [
        ('INFO', 'Информация'),
        ('WARNING', 'Предупреждение'),
        ('ERROR', 'Ошибка'),
    ]
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    message = models.TextField(verbose_name="Сообщение лога")

    def __str__(self):
        return f"Лог для задачи #{self.task.id} от {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = "Запись лога"
        verbose_name_plural = "Записи логов"
        ordering = ['-timestamp']

# --- СИГНАЛ (ИСПРАВЛЕННЫЙ) ---
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Создает UserProfile, ТОЛЬКО если User только что был создан.
    """
    if created:
        UserProfile.objects.create(user=instance)
