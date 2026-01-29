# core/forms.py
from django import forms
from .models import BiddingTask, UserProfile

class BiddingTaskForm(forms.ModelForm):
    class Meta:
        model = BiddingTask
        # Указываем поля, которые пользователь сможет заполнить.
        # Поле 'user' мы заполним автоматически.
        fields = ['ad_id', 'search_url', 'min_price', 'max_price','bid_step', 'target_position', 'is_active']


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        # Указываем поля, которые пользователь сможет редактировать
        fields = ['avito_client_id', 'avito_client_secret']
        widgets = {
            # Добавляем виджеты, чтобы поля выглядели как обычные текстовые, а не "шифрованные"
            'avito_client_id': forms.TextInput(attrs={'autocomplete': 'off'}),
            'avito_client_secret': forms.TextInput(attrs={'autocomplete': 'off'}),
        }