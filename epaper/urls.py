from django.urls import path
from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('upload/', views.upload_image_view, name='upload_image'),
    path('trigger/<int:image_id>/', views.trigger_update_view, name='trigger_update'),
    path('send-cmd/', views.send_cmd_view, name='send_cmd'),
]
