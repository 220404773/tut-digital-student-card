def unread_notices(request):
    """How many messages from the card office the signed-in student hasn't
    opened yet - the count on the bell in the menu bar."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or user.is_staff:
        return {}
    return {'unread_notice_count': user.notices.filter(read_at__isnull=True).count()}
