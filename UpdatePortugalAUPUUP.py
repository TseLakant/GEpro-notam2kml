import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from lxml import etree
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# --- ПАРАМЕТРЫ ---
IS_ONLINE = True
HTML_FILE = "AUP_UUP Details.htm"
INPUT_KML = "Data Base.kml"
OUTPUT_KML = "Active Regions.kml"
KML_NS = 'http://www.opengis.net/kml/2.2' # Правильное пространство имен для GEpro KML 2.2
FULL_COPY = ["SUPLEMENTOS ACTIVIDADES", "ESPAÇO AÉREO", "AERODROMOS E CAMPOS VOO"]  # Эти папки полетят в файл целиком
TRACEBACK_FILE = "Traceback.txt"


def download_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto('https://www.public.nm.eurocontrol.int/PUBPORTAL/gateway/spec/')

        # Перебираем возможные варианты времени, что бы перейти по ссылке, (каждая попытка +- 2 сек)
        flag = False
        now_utc = datetime.now(timezone.utc)
        floored_minute = (now_utc.minute // 30) * 30
        start_time = now_utc.replace(minute=floored_minute, second=0, microsecond=0)
        for trying_time in range(49):
            candidate_time = start_time - timedelta(minutes=30 * trying_time)
            need_page = candidate_time.strftime('%d/%m/%Y %H:%M')
            try:
                # Ожидаем появление новой вкладки (page) после клика
                with context.expect_page() as new_page_info:
                    # Кликаем по ссылке
                    page.get_by_text(need_page).click(timeout=2000)
                    print(f"⬇️Downloading EU table: {need_page}")
                    flag = True
                    break
            except PlaywrightTimeoutError:
                print(f"❌Dont have EU table: '{need_page}'")

        if not flag:
            raise RuntimeError(
                "Failed to find a downloadable EU table for the last 24 hours. "
                "The page format or availability may have changed."
            )
        # Это и есть та самая страница, которая открылась
        target_page = new_page_info.value

        # Ждем, пока JS отрисует таблицу (networkidle — нет запросов в течение 0.5 сек)
        target_page.wait_for_load_state("networkidle")

        # Получаем чистый HTML и сохраняем в файл
        html_code = target_page.content()
        with open(HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html_code)

        print(f"✅Table successfully saved in '{HTML_FILE}'")
        browser.close()

def process_ge_pro_kml(input_path, output_path, folders_to_copy, regions_dict):
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    tree = etree.parse(input_path, parser)
    root = tree.getroot()

    #Изменение имени файла
    doc_name = root.find(f".//{{{KML_NS}}}Document/{{{KML_NS}}}name")
    doc_name.text = output_path.replace(".kml", '').strip()

    # Поиск Document — главного контейнера Google Earth
    document = root.find("{{{}}}Document".format(KML_NS))
    if document is None:
        document = root

    # Находим все папки верхнего уровня
    folders = document.findall("{{{}}}Folder".format(KML_NS))

    for folder in folders:
        name_node = folder.find("{{{}}}name".format(KML_NS))
        folder_name = name_node.text if name_node is not None else "Unnamed Folder"

        if folder_name in folders_to_copy:
            print(f"📦 Full coping folder: {folder_name}")
            # Ничего не делаем, она остается в дереве со всем содержимым
        else:
            print(f"🔧 Processing folder contents: {folder_name}")
            # ПРИМЕР ОБРАБОТКИ: Удаляем всё, кроме Placemark с конкретным стилем
            # или просто удаляем папку, если она не нужна

            # Если нужно удалить папку целиком:
            # document.remove(folder)

            # Если нужно отфильтровать метки внутри этой папки:
            placemarks = folder.findall("{{{}}}Placemark".format(KML_NS))
            sorted_pm_count = 0

            found_in_kml = set()

            for pm in placemarks:
                # **Исправленный синтаксис lxml для поиска имени в рамках KML_NS**
                name_node = pm.find('{{{}}}name'.format(KML_NS))

                if name_node is not None and name_node.text:
                    kml_name = name_node.text.strip()
                    # Нормализация имени KML для сравнения со словарем: LP-D10 -> d10
                    ban_words = ['lp-', 'lp', 'area', 'fall', 'land']
                    pm_name_normalized = kml_name.lower()
                    for ban in ban_words:
                        pm_name_normalized = pm_name_normalized.replace(ban, '')
                    normalized_parts = pm_name_normalized.strip().split()
                    if not normalized_parts:
                        folder.remove(pm)
                        continue
                    pm_name_normalized = normalized_parts[0]

                    if pm_name_normalized in regions_dict:
                        # подсчитывает кол-во оставшихся регионов
                        sorted_pm_count += 1

                        if pm_name_normalized not in found_in_kml:
                            found_in_kml.add(pm_name_normalized)

                        # 1. Обновляем описание (используя исправленный синтаксис)
                        data_list = regions_dict[pm_name_normalized]
                        description_html = "\n".join(data_list)

                        desc_node = pm.find('{{{}}}description'.format(KML_NS))
                        if desc_node is None:
                            # Если description нет, создаем новый элемент с правильным NS
                            desc_node = etree.SubElement(pm, '{{{}}}description'.format(KML_NS))
                        else:
                            description_html = description_html + '\n \n' + str(desc_node.text)
                        desc_node.text = etree.CDATA(description_html)

                        # 2. Принудительный инлайновый красный стиль
                        # old_style_url = pm.find('{{{}}}styleUrl'.format(KML_NS))
                        # if old_style_url is not None:
                        #     pm.remove(old_style_url)

                        # Создание новых элементов также требует правильного синтаксиса NS
                        # colors = {'red': "ff2a00", 'blue': '0015ff', 'orange': 'FF8C00', 'black': '000000'}
                        # style = etree.SubElement(pm, '{{{}}}Style'.format(KML_NS))
                        # poly_style = etree.SubElement(style, '{{{}}}PolyStyle'.format(KML_NS))
                        # color = etree.SubElement(poly_style, '{{{}}}color'.format(KML_NS))
                        # color.text = "7f0000ff"
                        # outline = etree.SubElement(poly_style, '{{{}}}outline'.format(KML_NS))
                        # outline.text = "1"
                        # line_style = etree.SubElement(style, '{{{}}}LineStyle'.format(KML_NS))
                        # l_color = etree.SubElement(line_style, '{{{}}}color'.format(KML_NS))
                        # l_color.text = "ff0000ff"
                        # l_width = etree.SubElement(line_style, '{{{}}}width'.format(KML_NS))
                        # l_width.text = "2"
                    else:
                        folder.remove(pm)
            print(f'\tNumber of processed placemarks: {sorted_pm_count}')

    # Сохранение с корректным объявлением XML для Google Earth
    with open(output_path, 'wb') as f:
        f.write(etree.tostring(tree,
                               pretty_print=True,
                               xml_declaration=True,
                               encoding='utf-8'))

def parse_eaup_htm(file_path):
    # (Ваша функция parse_eaup_html остается без изменений)
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        soup = BeautifulSoup(f, 'html.parser')
    rows = soup.find_all('tr')
    seen_records = set()
    parsed_lp_regions = dict()
    print('🔍Founded regions:')
    for row in rows:
        cells = row.find_all(['td', 'th'])
        row_text = "|".join([c.get_text(strip=True) for c in cells if c.get_text(strip=True)])
        name_match = re.search(r'\b(LP(?:-?[A-Z0-9]+)+)\b', row_text, flags=re.IGNORECASE)
        if name_match:
            region_name = name_match.group(1).upper()
            if region_name == "LPA": continue
            times = re.findall(r'\d{2}:\d{2}', row_text)
            if len(times) > 4 or len(times) < 2: continue
            time_str = " - ".join(times[:2])
            raw_levels = re.findall(r'\b(?:\d{3}|SFC)\b', row_text, flags=re.IGNORECASE)
            altitudes = []
            for val in raw_levels:
                val = val.upper()
                if val == 'SFC' or val.isdigit() and int(val) == 0:
                    val = 'GND'
                elif val.isdigit() and int(val) < 245:
                    val = f"{int(val) * 100} ft"
                elif val.isdigit() and int(val) >= 245:
                    val = f'FL{val}'
                altitudes.append(val)
            clean_alts = altitudes[:2]
            alt_display = '/'.join(clean_alts) if clean_alts else "\033[31mNot specified\033[0m"
            record_key = f"{region_name}|{time_str}|{alt_display}"
            if record_key not in seen_records:
                seen_records.add(record_key)
                clean_region_name = region_name[2:].lower().replace('-', '').strip()
                time_alt_string = f"{time_str} | {alt_display} AMSL"
                print('', clean_region_name+' ', time_alt_string, sep='\t')
                if clean_region_name in parsed_lp_regions:
                    parsed_lp_regions[clean_region_name].append(time_alt_string)
                else:
                    parsed_lp_regions[clean_region_name] = [time_alt_string]
    return parsed_lp_regions


if __name__ == "__main__":
    try:
        if IS_ONLINE:
            download_page()
        print(f"🔧Start parsing '{HTML_FILE}'...")
        try:
            lp_regions = parse_eaup_htm(HTML_FILE)
        except FileNotFoundError:
            print(f"File '{HTML_FILE}' didn't found, check the file name with database, it must be called '{HTML_FILE}'")
            exit(1)

        if lp_regions:
            print(f"🔍Founded {len(lp_regions)} active regions in European AUP/UUP.")
            process_ge_pro_kml(INPUT_KML, OUTPUT_KML, FULL_COPY, lp_regions)
            print(f"✅Saved in '{OUTPUT_KML}'.")
        else:
            print("\033[31mRegions for update didn't found, KML didn't created, try later")
        print("\n\033[32mProcess finished\033[0m, without errors.")
    except Exception:
        tb = traceback.format_exc()
        print(f"\n\033[31mAn unexpected error occurred\033[0m. Traceback written to '{TRACEBACK_FILE}'.")
        with open(TRACEBACK_FILE, 'w', encoding='utf-8') as f:
            f.write(tb)
        print(tb, file=sys.stderr)
        sys.exit(1)
    finally:
        # Записываю в переменную enter, чтобы избавиться от бага с необходимостью дважды нажимать enter
        if sys.stdin.isatty():
            input("\nPress enter to exit...")

