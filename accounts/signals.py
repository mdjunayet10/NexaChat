from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


@receiver(
    post_save,
    sender=settings.AUTH_USER_MODEL,
    dispatch_uid="accounts.create_user_profile",
)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)
