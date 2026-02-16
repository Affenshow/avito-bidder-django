# main_app/forms.py - ИЗМЕНЕНИЯ ДЛЯ ЭТАПА 3

from django import forms
# +++ ИЗМЕНЕНИЕ: Импортируем новую модель AvitoAccount +++
from .models import BiddingTask, UserProfile, AvitoAccount

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ ШАГ 1: НОВАЯ ФОРМА ДЛЯ УПРАВЛЕНИЯ АККАУНТАМИ AVITO +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
class AvitoAccountForm(forms.ModelForm):
    """
    Форма для создания и редактирования аккаунтов Avito.
    """
    class Meta:
        model = AvitoAccount
        # Включаем все поля, кроме 'user', т.к. он будет присваиваться автоматически
        fields = ['name', 'avito_client_id', 'avito_client_secret']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Например, "Мой основной аккаунт"'}),
            'avito_client_id': forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Ваш Client ID'}),
            'avito_client_secret': forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Ваш Client Secret'}),
        }


class BiddingTaskForm(forms.ModelForm):
    # +++ ИЗМЕНЕНИЕ: Добавляем поле для выбора аккаунта Avito +++
    # Мы делаем его queryset пустым, т.к. будем заполнять его во view
    avito_account = forms.ModelChoiceField(
        queryset=AvitoAccount.objects.none(),
        label="Аккаунт Avito",
        empty_label=None, # Убираем пустой вариант "------"
        widget=forms.Select(attrs={'class': 'form-control'})
    )



    class Meta:
        model = BiddingTask
        
        fields = [
            # +++ ИЗМЕНЕНИЕ: Добавляем 'avito_account' в начало +++
            'avito_account', 
            'ad_id', 'search_url', 
            'min_price', 'max_price', 
            'target_position_min', 'target_position_max', 
            'bid_step', 
            'schedule',
            'daily_budget', 
            'is_active',
            'freeze_price_if_not_found',
        ]
        
        widgets = {
            'min_price': forms.NumberInput(attrs={'step': '1'}),
            'max_price': forms.NumberInput(attrs={'step': '1'}),
            'bid_step': forms.NumberInput(attrs={'step': '1'}),
            
            'schedule': forms.Textarea(attrs={'id': 'id_schedule_data'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'toggle-switch'}),
            'freeze_price_if_not_found': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    # +++ ИЗМЕНЕНИЕ: Конструктор для фильтрации аккаунтов +++
    def __init__(self, *args, **kwargs):
        # Извлекаем пользователя из переданных аргументов
        user = kwargs.pop('user', None)
        super(BiddingTaskForm, self).__init__(*args, **kwargs)
        
        if user:
            # Если пользователь передан, фильтруем аккаунты, чтобы показать только его
            self.fields['avito_account'].queryset = AvitoAccount.objects.filter(user=user)


# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ ШАГ 2: "ЧИНИМ" СТАРУЮ ФОРМУ USERPROFILEFORM +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        # +++ ИЗМЕНЕНИЕ: Убираем отсюда avito_client_id и avito_client_secret +++
        # Теперь эта форма пустая, но мы ее оставим, вдруг понадобится для других полей
        fields = []

daily_budget = forms.DecimalField(
    max_digits=10,
    decimal_places=2,
    required=True,  # ← теперь обязательно
    label="Дневный лимит трат (₽)",
    help_text="0 = без лимита. Нельзя оставить пустым.",
    widget=forms.NumberInput(attrs={
        'step': '1',
        'placeholder': 'Например 1000',
        'min': '0',           # нельзя отрицательный
        'value': '0'          # дефолтное значение в поле
    })
)

def clean_daily_budget(self):
    value = self.cleaned_data['daily_budget']
    if value is None:
        value = 0.00  # если пользователь стёр всё — ставим 0
    if value < 0:
        raise forms.ValidationError("Лимит не может быть отрицательным")
    return value