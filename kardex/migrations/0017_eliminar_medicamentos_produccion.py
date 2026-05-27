from django.db import migrations


def eliminar_medicamentos(apps, schema_editor):
    """Elimina medicamentos y cualquier movimiento residual para producción."""
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

    Documento.objects.all().update(documento_referencia=None)

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
    count, _ = Medicamento.objects.all().delete()

    print(f'    ✅ {count} medicamentos y todo el inventario eliminados. Sistema listo para producción.')


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0016_reset_movimientos_produccion"),
    ]

    operations = [
        migrations.RunPython(eliminar_medicamentos, migrations.RunPython.noop),
    ]
