# main_app/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import BiddingTask
from .tasks import run_bidding_for_task
import random


@receiver(post_save, sender=BiddingTask)
def auto_start_bidding(sender, instance, created, **kwargs):
    """Запускает биддер ТОЛЬКО при создании новой задачи."""
    if created and instance.is_active:
        delay = random.randint(10, 30)
        run_bidding_for_task.apply_async(args=[instance.id], countdown=delay)