from .models import GroupInvitation


def invitations(request):
    """Лічильник запрошень у групи для бейджа в сайдбарі.

    Лінивий: запит у базу йде лише тоді, коли шаблон справді читає змінну.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    def count():
        return GroupInvitation.objects.filter(user=user).count()

    return {"pending_invitations_count": count}
