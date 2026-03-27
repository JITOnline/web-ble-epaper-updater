from django.urls import path
from . import views

urlpatterns = [
    path("", views.index_view, name="index"),
    path("upload/", views.upload_image_view, name="upload_image"),
    path(
        "trigger/<int:image_id>/",
        views.trigger_update_view,
        name="trigger_update",
    ),
    path(
        "delete/<int:image_id>/", views.delete_image_view, name="delete_image"
    ),
    path("send-cmd/", views.send_cmd_view, name="send_cmd"),
    path("connect/", views.connect_device_view, name="connect_device"),
    path(
        "disconnect/", views.disconnect_device_view, name="disconnect_device"
    ),
    path(
        "generate-calendar/",
        views.generate_calendar_view,
        name="generate_calendar",
    ),
    path("bt-reset/", views.bt_reset_view, name="bt_reset"),
    path(
        "automation-status/",
        views.automation_status_view,
        name="automation_status",
    ),
]
