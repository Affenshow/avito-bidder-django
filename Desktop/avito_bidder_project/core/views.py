# core/views.py
from django.shortcuts import render, redirect # Добавляем redirect
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView
from django.contrib.auth.forms import UserCreationForm # Встроенная форма регистрации
from .models import BiddingTask
from django.contrib.auth.decorators import login_required # Декоратор для защиты
from .forms import BiddingTaskForm # Наша новая форма
from django.views.generic.edit import UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic.edit import DeleteView

@login_required # 1. Защищаем главную страницу. Теперь ее увидят только залогиненные пользователи.
def task_list_view(request):
    """
    Это представление (view) для отображения списка СВОИХ заданий.
    """
    # 2. Фильтруем задания не только по активности, но и по текущему пользователю.
    tasks = BiddingTask.objects.filter(user=request.user, is_active=True).order_by('-created_at')

    context = {
        'tasks': tasks
    }
    return render(request, 'core/task_list.html', context)

class SignUpView(CreateView):
    form_class = UserCreationForm
    # После успешной регистрации перенаправляем на страницу входа
    success_url = reverse_lazy('login')
    template_name = 'registration/signup.html'

@login_required # Эта "обертка" не пустит на страницу неавторизованных пользователей
def add_task_view(request):
    if request.method == 'POST':
        # Если форма была отправлена (метод POST)
        form = BiddingTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False) # Не сохраняем в БД сразу
            task.user = request.user       # Присваиваем заданию текущего пользователя
            task.save()                    # А теперь сохраняем
            return redirect('task-list')   # Перенаправляем на главную
    else:
        # Если страница просто открыта (метод GET)
        form = BiddingTaskForm() # Создаем пустую форму
    
    return render(request, 'core/add_task.html', {'form': form})

class TaskUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = BiddingTask
    form_class = BiddingTaskForm
    template_name = 'core/add_task.html' # Мы можем переиспользовать тот же шаблон!
    success_url = reverse_lazy('task-list')

    def test_func(self):
        # Эта функция проверяет, является ли текущий пользователь владельцем задачи.
        # Если нет - Django вернет ошибку 403 Forbidden (Доступ запрещен).
        task = self.get_object()
        return self.request.user == task.user
    
class TaskDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = BiddingTask
    success_url = reverse_lazy('task-list')
    template_name = 'core/task_confirm_delete.html'

    def test_func(self):
        # Та же самая проверка на владельца
        task = self.get_object()
        return self.request.user == task.user
    
@login_required
def ads_list_view(request):
    # Пока просто рендерим пустой шаблон
    return render(request, 'core/ads_list.html')

@login_required
def settings_view(request):
    # Пока просто рендерим пустой шаблон
    return render(request, 'core/settings.html')