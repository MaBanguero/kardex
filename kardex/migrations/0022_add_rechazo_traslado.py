# Generated manually: add rejection fields for traslado signing
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('kardex', '0021_add_aceptacion_traslado'),
    ]

    operations = [
        migrations.AddField(
            model_name='documento',
            name='rechazado',
            field=models.BooleanField(default=False, verbose_name='Rechazado por enfermera'),
        ),
        migrations.AddField(
            model_name='documento',
            name='motivo_rechazo',
            field=models.TextField(blank=True, null=True, verbose_name='Motivo de rechazo'),
        ),
        migrations.AlterField(
            model_name='documento',
            name='fecha_aceptacion',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de aceptación o rechazo'),
        ),
    ]
