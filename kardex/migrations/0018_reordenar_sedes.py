from django.db import migrations


def reordenar_sedes(apps, schema_editor):
    """Reordena sedes para producción: Principal, Puerto Tejada, Villa Rica, Padilla."""
    Ubicacion = apps.get_model('kardex', 'Ubicacion')

    # 1. Renombrar FarmaciaSede1 → Principal (bodega principal)
    principal = Ubicacion.objects.filter(nombre='FarmaciaSede1').first()
    if principal:
        principal.nombre = 'Principal'
        principal.es_bodega_principal = True
        principal.save()
        print('    ✏️  FarmaciaSede1 → Principal')
    else:
        principal = Ubicacion.objects.filter(nombre__iexact='Principal').first()
        if not principal:
            principal = Ubicacion.objects.create(nombre='Principal', es_bodega_principal=True)
            print('    🆕 Creada sede: Principal')

    # 2. Eliminar sedes que no corresponden
    for nombre_eliminar in ['Farmacia sede 2', 'FarmaciaSede1']:
        sede = Ubicacion.objects.filter(nombre=nombre_eliminar).first()
        if sede and sede.id != principal.id:
            sede.delete()
            print(f'    🗑️  Eliminada: {nombre_eliminar}')

    # 3. Asegurar sedes requeridas
    for nombre in ['Puerto Tejada', 'Villa Rica', 'Padilla']:
        if not Ubicacion.objects.filter(nombre__iexact=nombre).exists():
            Ubicacion.objects.create(nombre=nombre, es_bodega_principal=False)
            print(f'    🆕 Creada sede: {nombre}')
        else:
            print(f'    ✅ Ya existe: {nombre}')

    # 4. Solo Principal como bodega principal
    Ubicacion.objects.filter(es_bodega_principal=True).exclude(nombre='Principal').update(es_bodega_principal=False)

    print(f'\n    📋 Sedes finales:')
    for u in Ubicacion.objects.all().order_by('nombre'):
        bp = ' 🏭 BODEGA PRINCIPAL' if u.es_bodega_principal else ''
        print(f'      • {u.nombre}{bp}')


def reverse(apps, schema_editor):
    """No se puede revertir una eliminación de sedes."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0017_eliminar_medicamentos_produccion"),
    ]

    operations = [
        migrations.RunPython(reordenar_sedes, reverse),
    ]
