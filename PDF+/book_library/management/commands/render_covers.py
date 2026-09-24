import pymupdf
from book_library.models import Book
from book_library.pdf import render_cover
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Відрендерити обкладинки книгам, у яких їх ще нема."

    def handle(self, *args, **options):
        books = Book.objects.filter(cover="").order_by("pk")
        done = failed = 0

        for book in books.iterator():
            try:
                with book.file.open("rb") as handle:
                    data = handle.read()
                with pymupdf.open(stream=data, filetype="pdf") as document:
                    cover = render_cover(document)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"#{book.pk} «{book.title}»: {exc}")
                continue

            book.cover.save("cover.jpg", ContentFile(cover), save=False)
            book.save(update_fields=["cover"])
            done += 1
            self.stdout.write(f"#{book.pk} «{book.title}» → {book.cover.name}")

        summary = f"Готово: {done} відрендерено, {failed} з помилкою."
        self.stdout.write(self.style.SUCCESS(summary))
