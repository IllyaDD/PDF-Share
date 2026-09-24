from annotations.models import Annotation
from annotations.validators import merge_rects
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Злити перекриті прямокутники у вже збережених виділеннях."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показати, що змінилося б, але нічого не записувати.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbosity = options["verbosity"]
        changed = skipped = 0

        for annotation in Annotation.objects.order_by("pk").iterator():
            try:
                merged = merge_rects(annotation.coords)
            except (AttributeError, KeyError, TypeError) as exc:
                skipped += 1
                self.stderr.write(f"#{annotation.pk}: непридатні coords ({exc})")
                continue

            if merged == annotation.coords:
                continue

            changed += 1
            was, now = len(annotation.coords), len(merged)
            if verbosity >= 1:
                self.stdout.write(f"#{annotation.pk} с.{annotation.page_number}: {was} → {now}")
            if not dry_run:
                annotation.coords = merged
                annotation.save(update_fields=["coords"])

        if verbosity >= 1:
            verb = "змінилося б" if dry_run else "змінено"
            self.stdout.write(self.style.SUCCESS(f"{verb}: {changed}, пропущено: {skipped}"))
