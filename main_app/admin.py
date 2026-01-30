# core/admin.py

from django.contrib import admin
from .models import BiddingTask

@admin.register(BiddingTask)
class BiddingTaskAdmin(admin.ModelAdmin):
    """
    Настроенное представление для модели BiddingTask в админ-панели.
    """
    # Поля, которые будут отображаться в списке всех заданий
    list_display = ('id', 'ad_id', 'user', 'is_active', 'target_position_min', 'target_position_max', 'max_price', 'created_at')

    # Поля, по которым можно будет фильтровать список
    list_filter = ('is_active', 'user')

    # Поля, по которым будет работать поиск
    search_fields = ('ad_id', 'user__username')

    # Поля, которые будут ссылками на страницу редактирования
    list_display_links = ('id', 'ad_id')
