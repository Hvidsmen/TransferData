from django.http import HttpResponse
from transferdata.web.maintenance import is_paused


class PauseWrites:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in ('GET','HEAD','OPTIONS') and is_paused():
            return HttpResponse('Портал обновляется. Изменения не сохранены. Дождитесь завершения обновления.', status=503,
                headers={'Retry-After':'15'}, content_type='text/plain; charset=utf-8')
        return self.get_response(request)
