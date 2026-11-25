"""«Додати у свою бібліотеку»: легка копія книги каталогу.

Копія — звичайна книга користувача (теки, перейменування, доступ, видалення працюють як для
завантаженої), але без другого PDF у сховищі: file і cover посилаються на ті самі blob, що й
оригінал. Тому blob видаляє лише signals._delete_blob(), який рахує посилання.

Нотатки й прогрес у копії свої (інший pk), шару організатора з оригіналу на ній не видно,
а заміна PDF в оригіналі до копії не доходить.
"""

from django.db import transaction

from .access import catalog_folder_ids
from .models import Book


def catalog_books():
    """Книги, які можна додати собі: лише ті, що лежать у каталожних теках."""
    return Book.objects.filter(folder_id__in=catalog_folder_ids())


def add_to_library(user, book_id):
    """Робить копію каталожної книги в корені бібліотеки користувача. Повертає (копія, створено).

    Ідемпотентно: повторний виклик повертає наявну копію (UniqueConstraint(owner, source_book)).
    Book.DoesNotExist — книги нема в каталозі або вона й так належить користувачу.

    select_for_update на оригіналі: паралельне видалення оригіналу не може закомітитись між
    читанням оригіналу й вставкою копії, тож _delete_blob не зітре файл, на який копія от-от
    посилатиметься. Якщо видалення вже почалося (SET_NULL копій зроблено, DELETE чекає на
    блокування), воно впаде на перевірці FK і відкотиться — без втрати файлів.
    """
    with transaction.atomic():
        source = catalog_books().exclude(owner=user).select_for_update().get(pk=book_id)
        return Book.objects.get_or_create(
            owner=user,
            source_book=source,
            defaults={
                "title": source.title,
                "file": source.file.name,
                "cover": source.cover.name,
                "page_count": source.page_count,
                "file_size": source.file_size,
                "extracted_text": source.extracted_text,
            },
        )
