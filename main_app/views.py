# main_app/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
import json
# Импорты из нашего приложения
from .models import BiddingTask, UserProfile, TaskLog
from .forms import BiddingTaskForm, UserProfileForm
# --- ПРАВИЛЬНЫЙ, ПОЛНЫЙ ИМПОРТ ---
from .avito_api import get_avito_access_token, get_avito_user_id, get_balances
from .tasks import get_ad_position





@login_required
def task_list_view(request):
    tasks = BiddingTask.objects.filter(user=request.user)
    
    # --- "Подготавливаем" расписание для шаблона ---
    for task in tasks:
        if isinstance(task.schedule, str):
            try:
                task.schedule_list = json.loads(task.schedule)
            except json.JSONDecodeError:
                task.schedule_list = []
        else:
             task.schedule_list = task.schedule
    
    context = {'tasks': tasks}
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

            # --- РУЧНОЕ СОХРАНЕНИЕ РАСПИСАНИЯ ---
            # Берем JSON-строку напрямую из POST-запроса
            schedule_json = request.POST.get('schedule', '[]')
            # Просто записываем эту строку в текстовое поле модели
            task.schedule = schedule_json
            # --- КОНЕЦ РУЧНОГО СОХРАНЕНИЯ ---

            # Получаем доп. информацию (title, image_url)
            ad_data = get_ad_position(task.search_url, task.ad_id)
            if ad_data:
                task.title = ad_data.get('title')
                task.image_url = ad_data.get('image_url')
            
            task.save()
            return redirect('task-list')
        # Если форма невалидна, мы просто идем дальше
        # и рендерим ту же страницу, но с формой, содержащей ошибки
    else:
        # Для GET-запроса создаем пустую форму
        form = BiddingTaskForm()

    # --- ВОТ ГЛАВНЫЙ RETURN ДЛЯ GET-ЗАПРОСА ---
    # И для невалидного POST-запроса
    return render(request, 'main_app/add_task.html', {'form': form})


class TaskUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = BiddingTask
    form_class = BiddingTaskForm
    template_name = 'main_app/add_task.html'
    success_url = reverse_lazy('task-list')

    def test_func(self):
        task = self.get_object()
        return self.request.user == task.user
    
    # --- ДОБАВЛЯЕМ МЕТОД form_valid ---
    def form_valid(self, form):
        # Получаем JSON-строку напрямую из POST-запроса
        schedule_json = self.request.POST.get('schedule', '[]')
        # Присваиваем ее объекту перед сохранением
        self.object = form.save(commit=False)
        self.object.schedule = schedule_json
        self.object.save()
        return super().form_valid(form)
    # --- КОНЕЦ МЕТОДА ---


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
    profile = get_object_or_404(UserProfile, user=request.user)

    if request.method == 'POST':
        # Заполняем форму данными из POST-запроса
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            # Если форма валидна - сохраняем и делаем редирект
            form.save()
            return redirect('settings')
        # Если форма НЕ валидна, код просто идет дальше и рендерит
        # страницу с той же формой, но теперь она содержит ошибки
    else:
        # Для GET-запроса просто создаем форму, заполненную данными
        form = UserProfileForm(instance=profile)

    # --- Получение балансов ---
    real_balance, bonus_balance = None, None
    if profile.avito_client_id and profile.avito_client_secret:
        token = get_avito_access_token(profile.avito_client_id, profile.avito_client_secret)
        if token:
            user_id = get_avito_user_id(token)
            if user_id:
                balances = get_balances(token, user_id)
                if balances:
                    real_balance = balances.get('real')
                    bonus_balance = balances.get('bonus')
    
    # --- Определение контекста ---
    context = {
        'form': form, # Передаем в шаблон форму (либо пустую, либо с ошибками)
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

