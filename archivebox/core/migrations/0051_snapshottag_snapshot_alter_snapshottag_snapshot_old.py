# Generated by Django 5.0.6 on 2024-08-20 02:31

import django.db.models.deletion
from django.db import migrations, models


def update_snapshottag_ids(apps, schema_editor):
    Snapshot = apps.get_model("core", "Snapshot")
    SnapshotTag = apps.get_model("core", "SnapshotTag")
    num_total = SnapshotTag.objects.all().count()
    print(f'   Updating {num_total} SnapshotTag.snapshot_id values in place... (may take an hour or longer for large collections...)')
    for idx, snapshottag in enumerate(SnapshotTag.objects.all().only('snapshot_old_id').iterator()):
        assert snapshottag.snapshot_old_id
        snapshot = Snapshot.objects.get(old_id=snapshottag.snapshot_old_id)
        snapshottag.snapshot_id = snapshot.id
        snapshottag.save(update_fields=["snapshot_id"])
        assert str(snapshottag.snapshot_id) == str(snapshot.id)
        if idx % 100 == 0:
            print(f'Migrated {idx}/{num_total} SnapshotTag objects...')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0050_alter_snapshottag_snapshot_old'),
    ]

    operations = [
        migrations.AddField(
            model_name='snapshottag',
            name='snapshot',
            field=models.ForeignKey(blank=True, db_column='snapshot_id', null=True, on_delete=django.db.models.deletion.CASCADE, to='core.snapshot'),
        ),
        migrations.AlterField(
            model_name='snapshottag',
            name='snapshot_old',
            field=models.ForeignKey(db_column='snapshot_old_id', on_delete=django.db.models.deletion.CASCADE, related_name='snapshottag_old_set', to='core.snapshot', to_field='old_id'),
        ),
        migrations.RunPython(update_snapshottag_ids, reverse_code=migrations.RunPython.noop),
    ]