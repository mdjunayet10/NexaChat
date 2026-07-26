from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework.permissions import AllowAny

from config.settings import DEBUG, MEDIA_ROOT
from config.views import HealthCheckView

api_info = openapi.Info(
    title="Chatbot DRF Backend API",
    default_version="v1",
    description=(
        "API documentation for authentication, profile, chat sessions, "
        "messages, AI chat, AI usage and password reset."
    ),
)

schema_view = get_schema_view(
    api_info,
    public=True,
    permission_classes=[AllowAny],
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health-check"),
    path("api/accounts/", include("accounts.urls")),
    path("api/chats/", include("chats.urls")),
    re_path(
        r"^api/schema(?P<format>\.json|\.yaml)$",
        schema_view.without_ui(cache_timeout=0),
        name="schema-json",
    ),
    path(
        "api/docs/",
        schema_view.with_ui("swagger", cache_timeout=0),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        schema_view.with_ui("redoc", cache_timeout=0),
        name="redoc",
    ),
]

if DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": MEDIA_ROOT})
    ]
