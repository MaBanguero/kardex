from django.contrib.auth.models import Group


def user_roles(request):
    """Añade es_admin, es_regente y es_enfermera al context de todos los templates."""
    ctx = {
        'es_admin': False,
        'es_regente': False,
        'es_enfermera': False,
    }
    if request.user.is_authenticated:
        grupos = set(request.user.groups.values_list('name', flat=True))
        ctx['es_admin'] = 'ADMIN' in grupos
        ctx['es_regente'] = 'REGENTE' in grupos
        ctx['es_enfermera'] = 'ENFERMERA' in grupos
    return ctx
