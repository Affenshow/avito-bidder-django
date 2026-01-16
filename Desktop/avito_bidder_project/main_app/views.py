# main_app/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

# Импорты из нашего приложения
from .models import BiddingTask, UserProfile, TaskLog
from .forms import BiddingTaskForm, UserProfileForm
from .avito_api import get_avito_access_token, get_avito_balance


@login_required
def task_list_view(request):
    """
    Отображает список СВОИХ заданий.
    """
    tasks = BiddingTask.objects.filter(user=request.user)
    context = {
        'tasks': tasks
    }
    return render(request, 'main_app/task_list.html', context)


class SignUpView(CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy('login')
    template_name = 'registration/signup.html'


@login_required
def add_task_view(request):
    if request.method == 'POST':
        form = BiddingTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.save()
            return redirect('task-list')
    else:
        form = BiddingTaskForm()
    return render(request, 'main_app/add_task.html', {'form': form})


class TaskUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = BiddingTask
    form_class = BiddingTaskForm
    template_name = 'main_app/add_task.html'
    success_url = reverse_lazy('task-list')

    def test_func(self):
        task = self.get_object()
        return self.request.user == task.user


class TaskDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = BiddingTask
    success_url = reverse_lazy('task-list')
    template_name = 'main_app/task_confirm_delete.html'

    def test_func(self):
        task = self.get_object()
        return self.request.user == task.user


@login_required
def ads_list_view(request):
    return render(request, 'main_app/ads_list.html')


@login_required
def settings_view(request):
    # Принудительно получаем самый свежий профиль из БД, чтобы избежать кэширования
    profile = get_object_or_404(UserProfile, user=request.user)

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            return redirect('settings')
    else:
        form = UserProfileForm(instance=profile)

    # Инициализируем переменные
    real_balance = None
    bonus_balance = None

    # --- НАЧАЛО ЭКСПЕРИМЕНТА ---
    # Проверяем, что все три поля заполнены
    if profile.avito_client_id and profile.avito_client_secret and profile.avito_profile_id:
        token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
        if token:
            # Передаем ID профиля в функцию для запроса баланса
            balance_data = get_avito_balance(token, profile.avito_profile_id)
            if balance_data:
                real_balance = balance_data.get('real')
                bonus_balance = balance_data.get('bonus')
    # --- КОНЕЦ ЭКСПЕРИМЕНТА ---

    context = {
        'form': form,
        'real_balance': real_balance,
        'bonus_balance': bonus_balance,
    }
    return render(request, 'main_app/settings.html', context)


@login_required
def task_detail_view(request, pk):
    """
    Отображает детальную информацию и логи для одной задачи.
    """
    task = get_object_or_404(BiddingTask, pk=pk, user=request.user)
    logs = task.logs.all()
    context = {
        'task': task,
        'logs': logs,
    }
    return render(request, 'main_app/task_detail.html', context)

