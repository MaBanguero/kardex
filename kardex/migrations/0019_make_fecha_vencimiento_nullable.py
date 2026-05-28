from django.db import migrations, models


def set_default_date_for_null_vencimientos(apps, schema_editor):
    InventarioStock = apps.get_model('kardex', 'InventarioStock')
    from datetime import date, timedelta
    default_date = date.today() + timedelta(days=365 * 5)  # 5 años desde hoy
    # Set a default for existing nulls before changing the schema
    InventarioStock.objects.filter(fecha_vencimiento__isnull=True).update(
        fecha_vencimiento=default_date
    )


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0018_reordenar_sedes"),
    ]

    operations = [
        migrations.RunPython(
            set_default_date_for_null_vencimientos,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="inventariostock",
            name="fecha_vencimiento",
            field=models.DateField(null=True, blank=True),
        ),
    ]
