from django.db import migrations


def crear_sede_y_reasignar(apps, schema_editor):
    """Crea la sede Puerto Tejada y reasigna a todas las enfermeras."""
    Ubicacion = apps.get_model('kardex', 'Ubicacion')
    PerfilUsuario = apps.get_model('kardex', 'PerfilUsuario')

    sede, _ = Ubicacion.objects.get_or_create(
        nombre='Puerto Tejada',
        defaults={'es_bodega_principal': False}
    )

    count = PerfilUsuario.objects.filter(
        usuario__groups__name='ENFERMERA'
    ).exclude(
        usuario__username__in=['adela', 'enfermera']
    ).update(ubicacion_asignada=sede)

    print(f'    ✅ {count} enfermeras reasignadas a {sede.nombre} (ID: {sede.id})')


def reverse(apps, schema_editor):
    """No revertimos la creación de sede, solo dejamos las enfermeras sin sede."""
    Ubicacion = apps.get_model('kardex', 'Ubicacion')
    PerfilUsuario = apps.get_model('kardex', 'PerfilUsuario')
    try:
        sede = Ubicacion.objects.get(nombre='Puerto Tejada')
        PerfilUsuario.objects.filter(
            usuario__groups__name='ENFERMERA'
        ).exclude(
            usuario__username__in=['adela', 'enfermera']
        ).update(ubicacion_asignada=None)
    except Ubicacion.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0014_seed_enfermeras"),
    ]

    operations = [
        migrations.RunPython(crear_sede_y_reasignar, reverse),
    ]
