import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse

from .tasks import update_task_details
from .models import BiddingTask, UserProfile, TaskLog, AvitoAccount
from .forms import BiddingTaskForm, AvitoAccountForm
from .avito_api import get_avito_access_token, get_balances, get_user_ads, get_avito_user_id

logger = logging.getLogger(__name__)

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ СПИСОК АККАУНТОВ AVITO +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@login_required
def avito_account_list(request):
    accounts = AvitoAccount.objects.filter(user=request.user)
    for acc in accounts:
        token = get_avito_access_token(acc.avito_client_id, acc.avito_client_secret)
        if token:
            user_id = get_avito_user_id(token)
            if user_id:
                balances = get_balances(token, user_id)
                acc.real_balance = balances.get('real', 0)
                acc.bonus_balance = balances.get('bonus', 0)
            else:
                acc.real_balance = None
                acc.bonus_balance = None
        else:
            acc.real_balance = None
            acc.bonus_balance = None

    context = {'accounts': accounts}
    return render(request, 'main_app/avito_account_list.html', context)

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ CRUD ДЛЯ АККАУНТОВ AVITO +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

class AvitoAccountCreateView(LoginRequiredMixin, CreateView):
    model = AvitoAccount
    form_class = AvitoAccountForm
    template_name = 'main_app/avito_account_form.html'
    success_url = reverse_lazy('avito-account-list')

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

class AvitoAccountUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = AvitoAccount
    form_class = AvitoAccountForm
    template_name = 'main_app/avito_account_form.html'
    success_url = reverse_lazy('avito-account-list')

    def test_func(self):
        return self.request.user == self.get_object().user

class AvitoAccountDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = AvitoAccount
    template_name = 'main_app/avito_account_confirm_delete.html'
    success_url = reverse_lazy('avito-account-list')

    def test_func(self):
        return self.request.user == self.get_object().user

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ AJAX: СПИСОК ОБЪЯВЛЕНИЙ ДЛЯ ВЫБРАННОГО АККАУНТА +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@login_required
def get_ads_for_account(request, account_id):
    account = get_object_or_404(AvitoAccount, pk=account_id, user=request.user)
    token = get_avito_access_token(account.avito_client_id, account.avito_client_secret)
    if not token:
        return JsonResponse({'error': 'Не удалось получить токен Avito.'}, status=400)

    ads = get_user_ads(token)
    if ads is None:
        return JsonResponse({'error': 'Не удалось получить список объявлений.'}, status=400)

    return JsonResponse({'ads': ads})

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ СПИСОК ЗАДАЧ (ОБЗОР) +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@login_required
def task_list_view(request):
    # Получаем все задачи текущего пользователя
    tasks = BiddingTask.objects.filter(avito_account__user=request.user).select_related('avito_account')
    
    # +++ НАЧАЛО ИЗМЕНЕНИЙ +++
    # Получаем все аккаунты этого пользователя, чтобы построить кнопки-фильтры
    accounts = AvitoAccount.objects.filter(user=request.user)
    # +++ КОНЕЦ ИЗМЕНЕНИЙ +++

    # Ваша логика обработки расписания (оставляем без изменений)
    for task in tasks:
        try:
            task.schedule_list = json.loads(task.schedule)
        except (json.JSONDecodeError, TypeError):
            task.schedule_list = []
    
    context = {
        'tasks': tasks,
        'accounts': accounts, # <-- Передаем аккаунты в шаблон
    }
    return render(request, 'main_app/task_list.html', context)

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ УНИВЕРСАЛЬНАЯ VIEW ДЛЯ СОЗДАНИЯ/РЕДАКТИРОВАНИЯ ЗАДАЧИ +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

class TaskCreateUpdateView(LoginRequiredMixin, UpdateView):
    model = BiddingTask
    form_class = BiddingTaskForm
    template_name = 'main_app/add_task.html'
    success_url = reverse_lazy('task-list')

    def get_object(self, queryset=None):
        pk = self.kwargs.get('pk')
        if pk:
            return get_object_or_404(BiddingTask, pk=pk, avito_account__user=self.request.user)
        return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.user = self.request.user  # ← ЭТО КЛЮЧЕВАЯ СТРОКА — теперь user всегда заполнен
        self.object.schedule = self.request.POST.get('schedule', '[]')
        if not self.object.pk:
            self.object.title = f"Объявление №{self.object.ad_id}"
        self.object.save()
        update_task_details.delay(self.object.id)
        return redirect(self.success_url)

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ УДАЛЕНИЕ ЗАДАЧИ +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

class TaskDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = BiddingTask
    template_name = 'main_app/task_confirm_delete.html'
    success_url = reverse_lazy('task-list')

    def test_func(self):
        task = self.get_object()
        return self.request.user == task.avito_account.user

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ РЕГИСТРАЦИЯ +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

class SignUpView(CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy('login')
    template_name = 'registration/signup.html'

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ НАСТРОЙКИ (ЗАГЛУШКА) +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@login_required
def settings_view(request):
    return render(request, 'main_app/settings_stub.html')

# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# +++ ДЕТАЛИ ЗАДАЧИ +++
# +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@login_required
def task_detail_view(request, pk):
    task = get_object_or_404(BiddingTask, pk=pk, avito_account__user=request.user)
    logs = task.logs.order_by('-timestamp')[:50]  # последние 50 логов
    context = {'task': task, 'logs': logs}
    return render(request, 'main_app/task_detail.html', context)