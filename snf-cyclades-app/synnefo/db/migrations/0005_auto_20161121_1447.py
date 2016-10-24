# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0004_virtualmachine_key_name'),
    ]

    operations = [
        migrations.RenameField(
            model_name='image',
            old_name='mapfile',
            new_name='backend_id',
        ),
    ]
