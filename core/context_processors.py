from django.conf import settings
from django.urls import reverse

from .permissions import home_route


def app_meta(request):
    """Shared values made available to every template."""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        home_url = reverse(home_route(user))
    else:
        home_url = reverse("login")
    return {
        "APP_TITLE": settings.APP_TITLE,
        "HOME_URL": home_url,
    }
