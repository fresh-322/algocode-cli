from contextlib import redirect_stdout
from email.message import Message
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import algocode_cli as cli


HOME = '''<h1>Яндекс Кружок</h1><h2>Алгоритмы 2026-2027</h2>
<aside><h3>Полезная информация</h3><a href="https://t.me/example">Чат</a></aside>
<main><h3>Курсы</h3><div><a href="/a2026/">Параллель A</a></div>
<div><a href="/b2026/">Параллель B</a></div>
<div><a href="/page/archive/">Архивы</a></div></main>'''


def lesson(title, number, statements=True):
    return f'''<div class="lesson"><div>17 октября 2026 г.</div>
    <div><b>{title}</b></div><div>Контест
    <a href="https://ejudge.old.algocode.ru/cgi-bin/new-client?contest_id={number}&amp;locale_id=1">Вход</a>
    {f'<a href="/files/contest-{number}-ru.pdf">Условия</a>' if statements else ''}
    </div></div>'''


PAGE = '<h3>Занятия</h3>' + ''.join([
    lesson('Математика 1', 1), lesson('Строки 2', 2),
    lesson('ДИСТАНЦИОННЫЙ   ТУР 2', 3), lesson('Дисттур 3 — дорешка', 4),
    lesson('Дистур 4', 5), lesson('Графы 1', 6, False),
])


class Response(io.BytesIO):
    def __init__(self, data, filename=None, content_type='application/pdf'):
        super().__init__(data)
        self.url = 'https://algocode.ru/files/conditions.pdf'
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if filename:
            self.headers['Content-Disposition'] = f'attachment; filename="{filename}"'


class ParsingTests(unittest.TestCase):
    def test_courses_exclude_sidebar_and_resolve_urls(self):
        links = cli.parse_links(HOME, courses_only=True)
        self.assertEqual([l.title for l in links], ['Параллель A', 'Параллель B', 'Архивы'])
        self.assertEqual(links[1].url, 'https://algocode.ru/b2026/')

    def test_contests_keep_correct_link_pairs_and_filter_remote_rounds(self):
        contests = cli.parse_contests(PAGE, 'https://algocode.ru/b2026/')
        self.assertEqual(len(contests), 3)
        self.assertIn('Математика 1', contests[0].title)
        self.assertIn('Строки 2', contests[1].title)
        self.assertIn('Графы 1', contests[2].title)
        self.assertEqual(contests[1].statements, 'https://algocode.ru/files/contest-2-ru.pdf')
        self.assertIn('contest_id=2&locale_id=1', contests[1].login)
        self.assertIsNone(contests[2].statements)

    def test_table_layout(self):
        html = '<table><tr><td>3 октября 2026 г.</td><td>Строки 2</td></tr>'
        # Карточка может содержать вложенную таблицу со ссылками.
        html = '<article>3 октября 2026 г. Строки 2' + html + '''
        <tr><td>Контест</td><td><a href="/login">Вход</a></td>
        <td><a href="conditions.pdf">Условия</a></td></tr></table></article>'''
        contests = cli.parse_contests(html, 'https://algocode.ru/b2026/')
        self.assertEqual(len(contests), 1)
        self.assertEqual(contests[0].statements, 'https://algocode.ru/b2026/conditions.pdf')

    def test_archive_links_and_unsafe_urls(self):
        html = '''<h3>Архивы кружков и олимпиад</h3>
        <a href="/main/1/">Яндекс кружок</a><a href="javascript:alert(1)">JS</a>
        <a href="#top">Наверх</a>'''
        self.assertEqual(len(cli.parse_links(html)), 1)

    def test_interactive_navigation_and_link(self):
        output = io.StringIO()
        def fetch(url):
            return (HOME if url == cli.BASE_URL else PAGE), url
        with patch.object(cli, 'fetch_html', side_effect=fetch), \
             patch('builtins.input', side_effect=['2', '2', '1', '0', '0', '0']), \
             redirect_stdout(output):
            cli.browse(cli.BASE_URL, Path('/unused'))
        self.assertIn('contest_id=2&locale_id=1', output.getvalue())
        self.assertNotIn('ДИСТАНЦИОННЫЙ', output.getvalue())

    def test_all_remote_contests_do_not_leak_as_links(self):
        html = lesson('Дисттур 1', 9)
        output = io.StringIO()
        with patch.object(cli, 'fetch_html', return_value=(html, 'https://algocode.ru/b2026/')), \
             patch('builtins.input', side_effect=['0']), redirect_stdout(output):
            cli.browse('https://algocode.ru/b2026/', Path('/unused'))
        self.assertNotIn('Дисттур 1', output.getvalue())
        self.assertIn('нет доступных контестов', output.getvalue())


class DownloadTests(unittest.TestCase):
    def test_download_and_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli, 'open_url', side_effect=lambda _: Response(b'%PDF-1.7 test')):
                first = cli.download('https://algocode.ru/test.pdf', Path(directory))
                second = cli.download('https://algocode.ru/test.pdf', Path(directory))
            self.assertEqual(first.name, 'conditions.pdf')
            self.assertEqual(second.name, 'conditions-1.pdf')
            self.assertEqual(first.read_bytes(), b'%PDF-1.7 test')

    def test_html_and_empty_response_leave_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for data, content_type in [(b'<html>Login</html>', 'text/html'), (b'', 'application/pdf')]:
                with patch.object(cli, 'open_url', return_value=Response(data, content_type=content_type)):
                    with self.assertRaises(ValueError):
                        cli.download('https://algocode.ru/test.pdf', Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_download_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli, 'open_url', return_value=Response(b'%PDF')), \
                 patch.object(cli.shutil, 'copyfileobj', side_effect=OSError('disconnected')):
                with self.assertRaises(OSError):
                    cli.download('https://algocode.ru/test.pdf', Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
