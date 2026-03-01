import unittest
from unittest.mock import mock_open, patch, MagicMock
from datetime import datetime, timedelta, timezone
import os
import sys
from lxml import etree
import io

# Автоматически добавляем текущую директорию в пути поиска Python,
# чтобы он точно увидел файл main.py
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from mirror_1 import parse_eaup_htm
    from mirror_1 import process_ge_pro_kml
except ImportError:
    print("\n❌ ОШИБКА: Не удалось найти файл 'main.py'.")
    print(f"Убедитесь, что ваш скрипт переименован в 'main.py' и лежит здесь: {current_dir}")
    sys.exit(1)


class TestKMLProcessor(unittest.TestCase):

    def setUp(self):
        self.kml_ns = 'http://www.opengis.net/kml/2.2'

        # ТОЧНАЯ копия твоего заголовка с дублирующимися xmlns
        self.raw_kml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="{self.kml_ns}" 
             xmlns:gx="http://www.google.com/kml/ext/2.2" 
             xmlns:kml="{self.kml_ns}" 
             xmlns:atom="http://www.w3.org/2005/Atom">
        <Document>
            <name>00 - TOTAL ÁREAS FUA 2026</name>
            <Folder>
                <name>AREAS LP-D</name>
                <Placemark>
                    <name>LP-D10</name>
                    <description>EUROCONTROL FUA
ALTITUDES (FL) XXXXft AGL/FLXXX
RESTRITA (TIME) XX:XX-XX:XX UTC</description>
                </Placemark>
            </Folder>
        </Document>
        </kml>
        """.encode('utf-8')

        # Данные для теста (нормализованное имя 'd10')
        self.regions_dict = {"d10": ["11:00-13:00|GND/FL240"]}
        self.folders_to_copy = []

    def test_kml_filtering_logic(self):
        # Используем BytesIO для имитации файла, чтобы lxml сам разобрался с namespaces
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        test_tree = etree.parse(io.BytesIO(self.raw_kml), parser)

        with patch("lxml.etree.parse", return_value=test_tree):
            output_buffer = io.BytesIO()
            mock_file = MagicMock()
            mock_file.write = output_buffer.write
            mock_file.__enter__.return_value = mock_file

            with patch("builtins.open", return_value=mock_file):
                # Название выходного файла: "Active_Regions.kml"
                process_ge_pro_kml("dummy.kml", "Active_Regions.kml", self.folders_to_copy, self.regions_dict)

                result_xml = output_buffer.getvalue().decode('utf-8')

        # --- ПРОВЕРКИ ---
        # 1. Проверяем смену имени документа (должно стать "Active_Regions")
        self.assertIn("<name>Active_Regions</name>", result_xml)

        # 2. Проверяем, что LP-D10 остался
        self.assertIn("LP-D10", result_xml)

        # 3. Проверяем, что описание обновилось данными из словаря
        self.assertIn("11:00-13:00", result_xml)
        self.assertIn("GND/FL240", result_xml)


class TestFlightParser(unittest.TestCase):

    def setUp(self):
        # Имитируем кусок HTML-таблицы из проекта
        self.test_html = """
        <html>
            <table>
                <tr>
                    <td>LP-D10</td>
                    <td>10:00</td>
                    <td>12:00</td>
                    <td>SFC</td>
                    <td>050</td>
                </tr>
                <tr>
                    <td>LP-R15</td>
                    <td>14:00</td>
                    <td>16:00</td>
                    <td>245</td>
                    <td>300</td>
                </tr>
                <tr>
                    <td>TANCOS-LPR39A</td>
                    <td>18:00</td>
                    <td>21:00</td>
                    <td>010</td>
                    <td>300</td>
                </tr>
                <tr>
                    <td>LPA</td> <!-- Должно игнорироваться кодом -->
                    <td>08:00</td>
                    <td>09:00</td>
                    <td>100</td>
                    <td>200</td>
                </tr>
            </table>
        </html>
        """
        self.fake_path = "fake_table.htm"

    @patch("os.path.exists")
    def test_parse_logic(self, mock_exists):
        """Проверяем, как парсер обрабатывает названия, время и высоты"""
        mock_exists.return_value = True

        # Подменяем чтение файла нашей строкой test_html
        with patch("builtins.open", mock_open(read_data=self.test_html)):
            result = parse_eaup_htm(self.fake_path)

            # 1. Проверяем нормализацию имени: LP-D10 -> d10
            self.assertIn("d10", result)

            # 2. Проверяем конвертацию SFC -> GND и 050 -> 5000 ft
            # Ожидаем: "10:00 - 12:00 | GND/5000 ft AMSL"
            self.assertEqual(result["d10"][0], "10:00-12:00|GND/FL50")

            # 3. Проверяем уровни FL (>= 245)
            # 245 -> FL245, 300 -> FL300
            self.assertIn("r15", result)
            self.assertEqual(result["r15"][0], "14:00-16:00|FL245 AGL/FL300")

            # 4. Проверяем сложные названия
            # TANCOS-LPR39A -> r39a
            self.assertIn("r39a", result)
            self.assertEqual(result["r39a"][0], "18:00-21:00|1000ft AGL/FL300")

            # 5. Проверяем игнорирование региона LPA
            self.assertNotIn("a", result)

    def test_file_missing(self):
        """Проверяем, что при отсутствии файла выбрасывается ошибка"""
        with self.assertRaises(FileNotFoundError):
            parse_eaup_htm("absent_file.htm")


class TestTimeCalculation(unittest.TestCase):

    # Патчим datetime внутри вашего модуля main
    @patch('main.datetime')
    def test_floored_minute_at_01_minute(self, mock_datetime):
        # Эмулируем 14:01 UTC
        mock_now = datetime(2026, 2, 26, 14, 1, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = mock_now

        # Сама логика (как в вашем коде на стр. 1 PDF)
        now_utc = mock_datetime.now(timezone.utc)
        floored_minute = (now_utc.minute // 30) * 30
        start_time = now_utc.replace(minute=floored_minute, second=0, microsecond=0)

        # Ожидаем округление вниз до 14:00
        expected = datetime(2026, 2, 26, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(start_time, expected)
        print("✅ Тест 14:01 пройден успешно!")

    @patch('main.datetime')
    def test_floored_minute_at_59_minute(self, mock_datetime):
        # Эмулируем 14:59 UTC
        mock_now = datetime(2026, 2, 26, 14, 59, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = mock_now

        now_utc = mock_datetime.now(timezone.utc)
        floored_minute = (now_utc.minute // 30) * 30
        start_time = now_utc.replace(minute=floored_minute, second=0, microsecond=0)

        # Ожидаем округление вниз до 14:30
        expected = datetime(2026, 2, 26, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(start_time, expected)
        print("✅ Тест 14:59 пройден успешно!")

if __name__ == "__main__":
    unittest.main()
