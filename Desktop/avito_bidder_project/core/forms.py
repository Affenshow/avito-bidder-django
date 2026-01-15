# core/forms.py
from django import forms
from .models import BiddingTask

class BiddingTaskForm(forms.ModelForm):
    class Meta:
        model = BiddingTask
        # Указываем поля, которые пользователь сможет заполнить.
        # Поле 'user' мы заполним автоматически.
        fields = ['ad_id', 'search_url', 'min_price', 'max_price', 'target_position']
