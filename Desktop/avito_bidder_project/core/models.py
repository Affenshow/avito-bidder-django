# core/models.py
from django.db import models
from django.contrib.auth.models import User # Импортируем стандартную модель пользователя

class BiddingTask(models.Model):
    """
    Модель, описывающая одно задание для биддера.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    ad_id = models.BigIntegerField(verbose_name="ID объявления Avito")
    search_url = models.URLField(max_length=1000, verbose_name="URL для поиска")
    min_price = models.DecimalField(max_digits=10, decimal_places=2, default=10.00, verbose_name="Мин. цена")
    max_price = models.DecimalField(max_digits=10, decimal_places=2, default=50.00, verbose_name="Макс. цена")
    target_position = models.PositiveIntegerField(default=10, verbose_name="Целевая позиция (до)")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    def __str__(self):
        return f"Задание #{self.id} для объявления {self.ad_id}"

    class Meta:
        verbose_name = "Задание для биддера"
        verbose_name_plural = "Задания для биддера"
        ordering = ['-created_at']
