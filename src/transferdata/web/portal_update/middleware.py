from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from transferdata.web.maintenance import is_paused


class PauseWrites:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_paused():
            return self.get_response(request)
        update_url = reverse('portal_update')
        update_path = request.path.startswith(update_url)
        # Administrators follow progress instead of remaining on a stale workspace.
        # Authentication and update permissions remain enforced by the target view.
        if (request.method in ('GET', 'HEAD') and request.user.is_authenticated and request.user.is_superuser
                and not update_path and request.path not in ('/login/', '/logout/', '/favicon.ico')
                and not request.path.startswith('/static/')):
            if request.headers.get('X-ETL-Live') == '1':
                return HttpResponse('Переход к обновлению портала.', status=503,
                    headers={'X-ETL-Update-URL': update_url, 'Retry-After': '3'}, content_type='text/plain; charset=utf-8')
            return redirect(update_url)
        if request.method not in ('GET','HEAD','OPTIONS'):
            return HttpResponse('Портал обновляется. Изменения не сохранены. Дождитесь завершения обновления.', status=503,
                headers={'Retry-After':'15'}, content_type='text/plain; charset=utf-8')
        return self.get_response(request)




class PortalSessionMiddleware(SessionMiddleware):
    """Isolate portal cookies; adopt an old session only after normal Django authentication."""
    def process_request(self, request):
        request._portal_legacy_session = (
            settings.SESSION_COOKIE_NAME != 'sessionid'
            and settings.SESSION_COOKIE_NAME not in request.COOKIES
            and bool(request.COOKIES.get('sessionid')))
        if request._portal_legacy_session:
            request.COOKIES[settings.SESSION_COOKIE_NAME] = request.COOKIES['sessionid']
        super().process_request(request)

    def process_response(self, request, response):
        if getattr(request, '_portal_legacy_session', False) and getattr(request, 'user', None) is not None and request.user.is_authenticated:
            # Session lookup/signature/auth-hash validation happened through the usual middleware.
            request.session.modified = True
        return super().process_response(request, response)
