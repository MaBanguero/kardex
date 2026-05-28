from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kardex", "0019_make_fecha_vencimiento_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionsistema",
            name="alertas_habilitadas",
            field=models.BooleanField(
                default=True,
                help_text="Si está desactivado, no se muestran alertas de stock crítico ni semáforos de vencimiento.",
            ),
        ),
    ]
