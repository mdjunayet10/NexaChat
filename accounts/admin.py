from django.contrib import admin

from .models import PasswordResetToken, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "theme",
        "language",
        "email_notifications",
        "created_at",
        "updated_at",
    )
    list_filter = ("theme", "language", "email_notifications", "created_at")
    search_fields = ("user__username", "user__email", "bio", "phone")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "expires_at",
        "used_at",
        "valid",
        "created_at",
    )
    list_filter = ("created_at", "expires_at", "used_at")
    search_fields = ("user__username", "user__email", "token_hash")
    readonly_fields = ("id", "created_at")

    @admin.display(boolean=True, description="Valid")
    def valid(self, obj):
        return obj.is_valid
