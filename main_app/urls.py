# main_app/urls.py - ПОЛНАЯ ВЕРСИЯ ДЛЯ ЭТАПА 3

from django.urls import path
from . import views

urlpatterns = [
    # --- Задачи биддера (основной функционал) ---
    path('', views.task_list_view, name='task-list'),
    path('task/add/', views.TaskCreateUpdateView.as_view(), name='add-task'),
    path('task/<int:pk>/', views.task_detail_view, name='task-detail'),
    path('task/<int:pk>/edit/', views.TaskCreateUpdateView.as_view(), name='task-edit'),
    path('task/<int:pk>/delete/', views.TaskDeleteView.as_view(), name='task-delete'),
    path('api/tasks/bulk-update/', views.bulk_update_tasks, name='bulk-update-tasks'),
    path('api/tasks/bulk-delete/', views.bulk_delete_tasks, name='bulk-delete-tasks'),

    # +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    # +++ НОВЫЕ ПУТИ ДЛЯ УПРАВЛЕНИЯ АККАУНТАМИ AVITO +++
    # +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    path('accounts/', views.avito_account_list, name='avito-account-list'),
    path('accounts/add/', views.AvitoAccountCreateView.as_view(), name='avito-account-add'),
    path('accounts/<int:pk>/edit/', views.AvitoAccountUpdateView.as_view(), name='avito-account-edit'),
    path('accounts/<int:pk>/delete/', views.AvitoAccountDeleteView.as_view(), name='avito-account-delete'),

    # +++ НОВЫЙ AJAX-ПУТЬ ДЛЯ ПОЛУЧЕНИЯ ОБЪЯВЛЕНИЙ +++
    path('ajax/get-ads/<int:account_id>/', views.get_ads_for_account, name='ajax-get-ads'),

    # --- Старые и системные пути ---
    path('signup/', views.SignUpView.as_view(), name='signup'),
    
    # Страница ads-list больше не нужна, т.к. объявления будут загружаться динамически
    # path('ads/', views.ads_list_view, name='ads-list'), 
    
    # Страница settings теперь просто заглушка
    path('settings/', views.settings_view, name='settings'),
]

