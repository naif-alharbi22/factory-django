from django.conf import settings


def app_meta(request):
    """بيانات عامة متاحة في كل القوالب."""
    return {
        "APP_TITLE": settings.APP_TITLE,
    }
