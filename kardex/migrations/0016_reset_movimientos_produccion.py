from django.db import migrations


def reset_movimientos(apps, schema_editor):
    """Elimina todos los movimientos, medicamentos y resetea stock para producción."""
    Documento = apps.get_model('kardex', 'Documento')
    DocumentoDetalle = apps.get_model('kardex', 'DocumentoDetalle')
    TurnoEnfermera = apps.get_model('kardex', 'TurnoEnfermera')
    SolicitudStock = apps.get_model('kardex', 'SolicitudStock')
    Conciliacion = apps.get_model('kardex', 'Conciliacion')
    DetalleConciliacion = apps.get_model('kardex', 'DetalleConciliacion')
    CargaRIPS = apps.get_model('kardex', 'CargaRIPS')
    RegistroRIPS = apps.get_model('kardex', 'RegistroRIPS')
    MapeoRIPSMedicamento = apps.get_model('kardex', 'MapeoRIPSMedicamento')
    InventarioStock = apps.get_model('kardex', 'InventarioStock')
    Medicamento = apps.get_model('kardex', 'Medicamento')

    # 1. Nullificar referencias protegidas
    Documento.objects.all().update(documento_referencia=None)

    # 2. Eliminar en orden (hijos primero)
    DocumentoDetalle.objects.all().delete()
    Documento.objects.all().delete()
    TurnoEnfermera.objects.all().delete()
    SolicitudStock.objects.all().delete()
    DetalleConciliacion.objects.all().delete()
    Conciliacion.objects.all().delete()
    RegistroRIPS.objects.all().delete()
    CargaRIPS.objects.all().delete()
    MapeoRIPSMedicamento.objects.all().delete()
    InventarioStock.objects.all().delete()
    Medicamento.objects.all().delete()

    print('    ✅ Todo eliminado: movimientos, medicamentos y stock. Sistema listo para producción.')


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0015_fix_sede_puerto_tejada"),
    ]

    operations = [
        migrations.RunPython(reset_movimientos, migrations.RunPython.noop),
    ]
