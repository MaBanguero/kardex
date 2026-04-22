from django.db import migrations
from django.contrib.auth.hashers import make_password


def crear_datos_prueba(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Group = apps.get_model('auth', 'Group')
    Ubicacion = apps.get_model('kardex', 'Ubicacion')
    PerfilUsuario = apps.get_model('kardex', 'PerfilUsuario')

    # 1. Crear Sede de prueba
    sede, _ = Ubicacion.objects.get_or_create(nombre="FarmaciaSede1", es_bodega_principal=True)

    # 2. Configurar Grupos (Roles)
    admin_group, _ = Group.objects.get_or_create(name='ADMIN')
    regente_group, _ = Group.objects.get_or_create(name='REGENTE')
    enfermera_group, _ = Group.objects.get_or_create(name='ENFERMERA')

    # 3. Lista de usuarios solicitados para la prueba
    usuarios_test = [
        {'user': 'marvin', 'pass': '123', 'group': admin_group, 'is_super': True},
        {'user': 'regente', 'pass': 'FarmaciaSede1', 'group': regente_group, 'is_super': False},
        {'user': 'enfermera', 'pass': 'Esenorte3', 'group': enfermera_group, 'is_super': False},
    ]

    for data in usuarios_test:
        # get_or_create evita duplicados si ya ejecutaste el comando antes
        u, created = User.objects.get_or_create(
            username=data['user'],
            defaults={
                'password': make_password(data['pass']),
                'is_staff': True,
                'is_superuser': data['is_super']
            }
        )

        # Asignamos el rol
        u.groups.add(data['group'])

        # Vinculamos el PerfilUsuario para evitar el Error 500 al entrar al Dashboard
        PerfilUsuario.objects.get_or_create(
            usuario=u,
            defaults={
                'numero_identificacion': f'ID-{u.username.upper()}',
                'ubicacion_asignada': sede
            }
        )