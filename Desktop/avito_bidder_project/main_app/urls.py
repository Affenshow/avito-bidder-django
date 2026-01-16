# core/urls.py

from django.urls import path
from . import views
from .views import SignUpView,TaskUpdateView, TaskDeleteView # Импортируем наш новый класс

urlpatterns = [
    path('', views.task_list_view, name='task-list'),
    path('signup/', SignUpView.as_view(), name='signup'),
    path('ads/', views.ads_list_view, name='ads-list'), # <-- ДОБАВЬТЕ
    path('settings/', views.settings_view, name='settings'), # <-- ДОБАВЬТЕ
    path('add/', views.add_task_view, name='add-task'),
    path('task/<int:pk>/', views.task_detail_view, name='task-detail'),
     path('task/<int:pk>/edit/', TaskUpdateView.as_view(), name='task-edit'),
     path('task/<int:pk>/delete/', TaskDeleteView.as_view(), name='task-delete'),
]
