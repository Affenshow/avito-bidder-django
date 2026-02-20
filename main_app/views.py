import json
import logging
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .tasks import update_task_details
from .models import BiddingTask, UserProfile, TaskLog, AvitoAccount
from .forms import BiddingTaskForm, AvitoAccountForm
from .avito_api import get_avito_access_token, get_balances, get_user_ads, get_avito_user_id

logger = logging.getLogger(__name__)


# === СПИСОК АККАУНТОВ AVITO ===

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


# === CRUD ДЛЯ АККАУНТОВ AVITO ===

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


# === AJAX: СПИСОК ОБЪЯВЛЕНИЙ ДЛЯ ВЫБРАННОГО АККАУНТА ===

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


# === СПИСОК ЗАДАЧ (ОБЗОР) ===

@login_required
def task_list_view(request):
    tasks = BiddingTask.objects.filter(
        avito_account__user=request.user
    ).select_related('avito_account')
    
    accounts = AvitoAccount.objects.filter(user=request.user)

    for task in tasks:
        try:
            task.schedule_list = json.loads(task.schedule)
        except (json.JSONDecodeError, TypeError):
            task.schedule_list = []
    
    context = {
        'tasks': tasks,
        'accounts': accounts,
    }
    return render(request, 'main_app/task_list.html', context)


# === СОЗДАНИЕ/РЕДАКТИРОВАНИЕ ЗАДАЧИ ===

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
        self.object.user = self.request.user
        self.object.schedule = self.request.POST.get('schedule', '[]')
        if not self.object.pk:
            self.object.title = f"Объявление №{self.object.ad_id}"
        self.object.save()
        update_task_details.delay(self.object.id)
        return redirect(self.success_url)


# === УДАЛЕНИЕ ЗАДАЧИ ===

class TaskDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = BiddingTask
    template_name = 'main_app/task_confirm_delete.html'
    success_url = reverse_lazy('task-list')

    def test_func(self):
        task = self.get_object()
        return self.request.user == task.avito_account.user


# === РЕГИСТРАЦИЯ ===

class SignUpView(CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy('login')
    template_name = 'registration/signup.html'


# === НАСТРОЙКИ ===

@login_required
def settings_view(request):
    return render(request, 'main_app/settings_stub.html')


# === ДЕТАЛИ ЗАДАЧИ ===

@login_required
def task_detail_view(request, pk):
    task = get_object_or_404(BiddingTask, pk=pk, avito_account__user=request.user)
    logs = task.logs.order_by('-timestamp')[:50]
    
    # Парсим расписание для отображения
    try:
        task.schedule_list = json.loads(task.schedule)
    except (json.JSONDecodeError, TypeError):
        task.schedule_list = []
    
    context = {'task': task, 'logs': logs}
    return render(request, 'main_app/task_detail.html', context)


# === МАССОВЫЕ ОПЕРАЦИИ ===

@login_required
@require_POST
def bulk_update_tasks(request):
    """Массовое обновление задач"""
    try:
        data = json.loads(request.body)
        task_ids = data.get('task_ids', [])
        
        tasks = BiddingTask.objects.filter(
            id__in=task_ids,
            avito_account__user=request.user
        )
        
        update_fields = {}
        
        if 'is_active' in data:
            update_fields['is_active'] = data['is_active']
        if 'target_position_min' in data:
            update_fields['target_position_min'] = data['target_position_min']
        if 'target_position_max' in data:
            update_fields['target_position_max'] = data['target_position_max']
        if 'min_price' in data:
            update_fields['min_price'] = data['min_price']
        if 'max_price' in data:
            update_fields['max_price'] = data['max_price']
        if 'bid_step' in data:
            update_fields['bid_step'] = data['bid_step']
        
        if update_fields:
            tasks.update(**update_fields)
        
        return JsonResponse({
            'status': 'ok',
            'updated': tasks.count()
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=400)


@login_required
@require_POST
def bulk_delete_tasks(request):
    """Массовое удаление задач"""
    try:
        data = json.loads(request.body)
        task_ids = data.get('task_ids', [])
        
        tasks = BiddingTask.objects.filter(
            id__in=task_ids,
            avito_account__user=request.user
        )
        count = tasks.count()
        tasks.delete()
        
        return JsonResponse({
            'status': 'ok',
            'deleted': count
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=400)
    

@login_required
def api_account_items(request, account_id):
    """API: список объявлений аккаунта Avito"""
    from main_app.avito_api import get_avito_access_token
    
    account = get_object_or_404(AvitoAccount, id=account_id, user=request.user)
    token = get_avito_access_token(account.avito_client_id, account.avito_client_secret)
    
    if not token:
        return JsonResponse({"error": "Не удалось получить токен"}, status=400)
    
    headers = {"Authorization": f"Bearer {token}"}
    
    all_items = []
    page = 1
    
    while True:
        resp = requests.get(
            "https://api.avito.ru/core/v1/items",
            headers=headers,
            params={"per_page": 100, "page": page, "status": "active"},
            timeout=15
        )
        
        if resp.status_code != 200:
            break
        
        data = resp.json()
        resources = data.get("resources", [])
        
        if not resources:
            break
        
        all_items.extend(resources)
        
        if len(resources) < 100:
            break
        
        page += 1
    
    # Убираем уже добавленные
    existing_ad_ids = set(
        BiddingTask.objects.filter(
            avito_account=account
        ).values_list("ad_id", flat=True)
    )
    
    items = []
    for item in all_items:
        items.append({
            "id": item["id"],
            "title": item.get("title", ""),
            "price": item.get("price", 0),
            "url": item.get("url", ""),
            "address": item.get("address", ""),
            "category": item.get("category", {}).get("name", ""),
            "status": item.get("status", ""),
            "already_added": item["id"] in existing_ad_ids,
        })
    
    return JsonResponse({"items": items, "total": len(items)})


@login_required
def add_task_page(request):
    """Страница добавления задач"""
    accounts = AvitoAccount.objects.filter(user=request.user)
    return render(request, 'main_app/add_task.html', {'accounts': accounts})


@login_required
@require_POST
def api_add_tasks(request):
    """API: массовое добавление задач"""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    account_id = data.get("account_id")
    items = data.get("items", [])

    if not account_id or not items:
        return JsonResponse({"success": False, "error": "Нет данных"}, status=400)

    account = get_object_or_404(AvitoAccount, id=account_id, user=request.user)

    created = 0
    added_ids = []

    for item in items:
        ad_id = item.get("ad_id")
        if not ad_id:
            continue

        # Пропускаем дубликаты
        if BiddingTask.objects.filter(avito_account=account, ad_id=ad_id).exists():
            continue

        task = BiddingTask.objects.create(
            user=request.user,
            avito_account=account,
            ad_id=ad_id,
            title=item.get("title", ""),
            max_bid_kopecks=int(float(item.get("max_bid", 300)) * 100),
            strategy=item.get("strategy", "match_position"),
            target_position=int(item.get("target_position", 1)),
            is_active=True,
        )

        # Пробуем получить картинку через парсинг URL
        url = item.get("url", "")
        if url:
            try:
                from main_app.avito_api import get_item_info
                token = get_avito_access_token(account.avito_client_id, account.avito_client_secret)
                info = get_item_info(token, ad_id)
                if info and info.get("image_url"):
                    task.image_url = info["image_url"]
                    task.save(update_fields=["image_url"])
            except Exception:
                pass

        created += 1
        added_ids.append(ad_id)

    return JsonResponse({
        "success": True,
        "created": created,
        "added_ids": added_ids,
    })