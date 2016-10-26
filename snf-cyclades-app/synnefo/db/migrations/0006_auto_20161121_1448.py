# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0005_auto_20161121_1447'),
    ]

    operations = [
        migrations.AlterField(
            model_name='image',
            name='version',
            field=models.CharField(max_length=128),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='virtualmachine',
            name='image_version',
            field=models.CharField(max_length=128, null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='volume',
            name='source_version',
            field=models.CharField(max_length=128, null=True),
            preserve_default=True,
        ),
    ]
