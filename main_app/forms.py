# core/forms.py
from django import forms
from .models import BiddingTask, UserProfile

class BiddingTaskForm(forms.ModelForm):
    class Meta:
        model = BiddingTask
        
        # --- ВОЗВРАЩАЕМ ВСЕ ПОЛЯ В fields ---
        fields = [
            'ad_id', 'search_url', 
            'min_price', 'max_price', 
            'target_position_min', 'target_position_max', 
            'bid_step', 'schedule', 'is_active'
        ]
        
        # --- УКАЗЫВАЕМ ТОЛЬКО ВИДЖЕТЫ И ИХ НАСТРОЙКИ ---
        widgets = {
            'min_price': forms.NumberInput(attrs={'step': '1'}),
            'max_price': forms.NumberInput(attrs={'step': '1'}),
            'bid_step': forms.NumberInput(attrs={'step': '1'}),
            
            'schedule': forms.Textarea(attrs={'id': 'id_schedule_data'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'toggle-switch'}),
        }


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