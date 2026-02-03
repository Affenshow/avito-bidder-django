# main_app/views.py
import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .tasks import update_task_details
# Импорты из нашего приложения
from .models import BiddingTask, UserProfile, TaskLog
from .forms import BiddingTaskForm, UserProfileForm
# --- ПРАВИЛЬНЫЙ, ПОЛНЫЙ ИМПОРТ ---
from .avito_api import get_avito_access_token, get_avito_user_id, get_balances
from .tasks import get_ad_position


logger = logging.getLogger(__name__) # <-- И ЗДЕСЬ


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
    """
    Обрабатывает создание НОВОЙ задачи.
    """
    if request.method == 'POST':
        form = BiddingTaskForm(request.POST)
        if form.is_valid():
            # Сохраняем основные данные, введенные пользователем
            task = form.save(commit=False)
            task.user = request.user
            
            # Вручную сохраняем расписание, так как оно не в форме
            schedule_json = request.POST.get('schedule', '[]')
            task.schedule = schedule_json

            task.save() # Сохраняем, чтобы получить task.id

            # --- ЗАПУСКАЕМ ФОНОВУЮ ЗАДАЧУ ---
            # Передаем ей ID только что созданной задачи
            update_task_details.delay(task.id)
            logger.info(f"Задача #{task.id} создана. Запущена фоновая задача для получения деталей.")

            return redirect('task-list')
    else:
        # Для GET-запроса просто показываем пустую форму
        form = BiddingTaskForm()

    return render(request, 'main_app/add_task.html', {'form': form})


class TaskUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = BiddingTask
    form_class = BiddingTaskForm
    template_name = 'main_app/add_task.html'
    success_url = reverse_lazy('task-list')

    def form_valid(self, form):
        """
        Переопределяем метод, чтобы вручную сохранить расписание.
        """
        # Получаем объект задачи, но пока не сохраняем в БД
        self.object = form.save(commit=False)
        
        # Вручную берем JSON-строку из POST-запроса
        schedule_json = self.request.POST.get('schedule', '[]')
        self.object.schedule = schedule_json
        
        # Теперь сохраняем все изменения
        self.object.save()
        
        # Мы не будем повторно запускать парсинг title/image при редактировании,
        # так как они уже должны были быть получены при создании.
        
        # Вызываем родительский метод, который сделает редирект
        return super().form_valid(form)

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

