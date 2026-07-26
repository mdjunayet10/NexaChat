from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ChatMessage, ChatSession


@receiver(
    post_save,
    sender=ChatMessage,
    dispatch_uid="chats.update_session_last_message_at",
)
def update_session_last_message_at(sender, instance, created, **kwargs):
    if created:
        ChatSession.objects.filter(pk=instance.session_id).update(
            last_message_at=instance.created_at
        )
