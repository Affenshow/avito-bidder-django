# main_app/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import BiddingTask
from .tasks import run_bidding_for_task
import random

@receiver(post_save, sender=BiddingTask)
def auto_start_bidding(sender, instance, created, **kwargs):
    if instance.is_active:  # запускаем только если задача активна
        # Первый запуск через 3–8 минут после создания/активации
        delay = random.randint(180, 480)  # 3–8 минут рандомно
        run_bidding_for_task.apply_async(args=[instance.id], countdown=delay)