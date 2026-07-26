from django.urls import path

from .views import (
    AIUsageListView,
    AIUsageSummaryView,
    ChatMessageDetailView,
    ChatSessionDetailView,
    ChatSessionListCreateView,
    SessionAIUsageListView,
    SessionChatView,
    SessionMessageListCreateView,
)

app_name = "chats"

urlpatterns = [
    path("usage/", AIUsageListView.as_view(), name="usage-list"),
    path(
        "usage/summary/",
        AIUsageSummaryView.as_view(),
        name="usage-summary",
    ),
    path(
        "sessions/",
        ChatSessionListCreateView.as_view(),
        name="session-list-create",
    ),
    path(
        "sessions/<uuid:pk>/",
        ChatSessionDetailView.as_view(),
        name="session-detail",
    ),
    path(
        "sessions/<uuid:session_id>/messages/",
        SessionMessageListCreateView.as_view(),
        name="session-message-list-create",
    ),
    path(
        "sessions/<uuid:session_id>/usage/",
        SessionAIUsageListView.as_view(),
        name="session-usage-list",
    ),
    path(
        "sessions/<uuid:session_id>/chat/",
        SessionChatView.as_view(),
        name="session-chat",
    ),
    path(
        "messages/<uuid:pk>/",
        ChatMessageDetailView.as_view(),
        name="message-detail",
    ),
]
