# core/models.py
from django.db import models
from django.contrib.auth.models import User # Импортируем стандартную модель пользователя
# --- ПРАВИЛЬНЫЙ ИМПОРТ ---
from encrypted_model_fields.fields import EncryptedCharField
from django.conf import settings


class BiddingTask(models.Model):
    """
    Модель, описывающая одно задание для биддера.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    ad_id = models.BigIntegerField(verbose_name="ID объявления Avito")
    search_url = models.URLField(max_length=1000, verbose_name="URL для поиска")
    min_price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00, verbose_name="Мин. цена")
    max_price = models.DecimalField(max_digits=10, decimal_places=2, default=50.00, verbose_name="Макс. цена")
    bid_step = models.DecimalField(max_digits=10, decimal_places=2, default=1.00, verbose_name="Шаг ставки (₽)")
    target_position = models.PositiveIntegerField(default=10, verbose_name="Целевая позиция (до)")
    if settings.DEBUG:
        schedule = models.TextField(default="[]", blank=True, verbose_name="Расписание (JSON-строка)")
    else:
        schedule = models.JSONField(default=list, blank=True, verbose_name="Расписание работы (интервалы)")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    def __str__(self):
        return f"Задание #{self.id} для объявления {self.ad_id}"

    class Meta:
        verbose_name = "Задание для биддера"
        verbose_name_plural = "Задания для биддера"
        ordering = ['-created_at']


class UserProfile(models.Model):
    """
    Профиль пользователя для безопасного хранения API-ключей и других настроек.
    """
    # Связь "один-к-одному" со стандартной моделью User.
    # related_name='profile' позволяет нам обращаться к профилю через user.profile
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # Шифруемые поля для хранения ключей.
    # В базе они будут храниться как зашифрованный текст.
    avito_client_id = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="Avito Client ID")
    avito_client_secret = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="Avito Client Secret")

    def __str__(self):
        return f"Профиль для {self.user.username}"

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"


# ===============================================================
# === СИГНАЛЫ ДЛЯ АВТОМАТИЧЕСКОГО СОЗДАНИЯ/СОХРАНЕНИЯ ПРОФИЛЯ ===
# ===============================================================
# Этот код гарантирует, что профиль будет создан для каждого нового пользователя.

from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Создает UserProfile, если User только что создан.
    В ином случае, просто сохраняет существующий профиль.
    """
    if created:
        UserProfile.objects.create(user=instance)
    instance.profile.save()

class TaskLog(models.Model):
    """
    Модель для хранения логов выполнения одной конкретной задачи.
    """
    # Связь с родительской задачей. При удалении задачи, все ее логи тоже удалятся.
    task = models.ForeignKey(BiddingTask, on_delete=models.CASCADE, related_name='logs')
    
    # Время, когда произошло событие
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # Уровень сообщения (для возможной раскраски в будущем)
    LEVEL_CHOICES = [
        ('INFO', 'Информация'),
        ('WARNING', 'Предупреждение'),
        ('ERROR', 'Ошибка'),
    ]
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')

    # Текст самого сообщения
    message = models.TextField(verbose_name="Сообщение лога")

    def __str__(self):
        return f"Лог для задачи #{self.task.id} от {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = "Запись лога"
        verbose_name_plural = "Записи логов"
        # Сортировка по умолчанию: самые новые логи - наверху
        ordering = ['-timestamp']