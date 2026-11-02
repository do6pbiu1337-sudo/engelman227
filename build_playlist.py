#!/usr/bin/env python3
"""Собирает свой список каналов playlist.m3u из двух открытых списков iptv-org.

Что делает:
1. Скачивает каналы России (countries/ru.m3u) и каналы на русском языке (languages/rus.m3u).
2. Оставляет только русскоязычные: канал из списка «Россия» остаётся, только если он есть
   и в списке «русский язык».
3. Убирает категории из EXCLUDED_GROUPS (религия, магазины).
4. Склеивает одинаковые каналы: SD и HD одного канала становятся одним каналом, у которого
   все ссылки идут подряд, рабочие и HD первыми. Сдвиги времени (+2, +4, +7) остаются отдельно.
5. Убирает только явно мёртвые ссылки: сервер не существует (нет DNS) или ответ 404/410.
   Остальные ошибки (403, таймаут) не трогает: из-за границы гео-блокировка выглядит так же.

Только стандартная библиотека Python, запускается и на компьютере, и в GitHub Actions:
    python build_playlist.py            # пишет playlist.m3u рядом
"""
import concurrent.futures
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

SOURCES = [
    "https://iptv-org.github.io/iptv/languages/rus.m3u",   # первым: он задаёт, что русскоязычное
    "https://iptv-org.github.io/iptv/countries/ru.m3u",
]
EXCLUDED_GROUPS = {"Religious", "Shop"}
# Категории iptv-org -> свои русские. У канала с несколькими категориями берётся первая по порядку
# CATEGORY_ORDER, так что каждый канал лежит ровно в одной категории.
CATEGORIES = {
    "General": "Общие",
    "News": "Новости", "Business": "Новости", "Legislative": "Новости", "Weather": "Новости",
    "Movies": "Кино и сериалы", "Series": "Кино и сериалы", "Classic": "Кино и сериалы",
    "Kids": "Детские", "Animation": "Детские", "Family": "Развлечения",
    "Sports": "Спорт",
    "Music": "Музыка",
    "Documentary": "Познавательные", "Science": "Познавательные", "Education": "Познавательные",
    "Travel": "Познавательные", "Culture": "Познавательные", "Outdoor": "Познавательные",
    "Auto": "Познавательные",
    "Entertainment": "Развлечения", "Comedy": "Развлечения", "Lifestyle": "Развлечения",
    "Cooking": "Развлечения", "Relax": "Развлечения",
}
CATEGORY_ORDER = ["Детские", "Спорт", "Музыка", "Новости", "Кино и сериалы", "Познавательные",
                  "Развлечения", "Общие"]
OTHER_CATEGORY = "Разное"
# Only these channels go into the playlist, under these names: the owner's own channel list (his provider's
# lineup), matched by hand to iptv-org ids. Keys are channel_key() values.
CHANNELS = {
    "ChannelOne.ru": "Первый канал", "Russia1.ru": "Россия 1", "NTV.ru": "НТВ", "Channel5.ru": "Пятый канал",
    "RussiaK.ru": "Россия Культура", "Russia24.ru": "Россия 24", "Carousel.ru": "Карусель", "OTR.ru": "ОТР",
    "TVCentr.ru": "ТВЦ", "RENTV.ru": "РЕН ТВ", "STS.ru": "СТС", "Domashniy.ru": "Домашний", "TV3.ru": "ТВ-3",
    "Friday.ru": "Пятница", "Zvezda.ru": "Звезда", "Mir.ru": "Мир", "TNT.ru": "ТНТ", "MuzTV.ru": "МУЗ-ТВ",
    "Volga.ru": "Волга", "Channel8.ru": "8 канал", "Che.ru": "Че", "Solnce.ru": "Солнце", "U.ru": "Ю",
    "Russkiyroman.ru": "Русский роман", "Kinouzhas.ru": "Киноужас", "Domkino.ru": "Дом кино",
    "Subbota.ru": "Суббота!", "Kineko.ru": "Кинеко", "Saphire.ru": "Сапфир", "red.ru": ".red",
    "scifi.ru": ".sci-fi", "Kinokomedija.ru": "Кинокомедия", "STARTAir.ru": "START Air", "KinoTV.ru": "Кино ТВ",
    "Kinomix.ru": "Киномикс", "NasheNovoeKino.ru": "Наше новое кино", "STSLove.ru": "СТС Love",
    "black.ru": ".black", "A2.ru": "A2", "A1.ru": "A1", "IllusionPlus.ru": "Иллюзион+", "Hollywood.ru": "Hollywood",
    "NST.ru": "НСТ", "RusskiyIllusion.ru": "Русский иллюзион", "vijuTV1000russkoe.ru": "viju TV1000 русское",
    "vijuTV1000.ru": "viju TV1000", "vijuTV1000action.ru": "viju TV1000 action", "Cinema.ru": "Cinema",
    "Komedia.ru": "Комедия", "Kinoseriya.ru": "Киносерия", "StarFamily.ru": "Star Family",
    "StarCinema.ru": "Star Cinema", "NTVHit.ru": "НТВ Хит", "Pobeda.ru": "Победа",
    "FeniksplusKino.ru": "Феникс+ Кино", "NTVSeries.ru": "НТВ Сериал", "Vremya.ru": "Время", "FAN.ru": "FAN",
    "STARTWorld.ru": "Start World", "India.ru": "Индия", "TNT4.ru": "ТНТ4", "KVNTV.ru": "КВН ТВ",
    "1HDMusicTelevision.ru": "1HD Music Television", "2x2.ru": "2x2", "Telecafe.ru": "Телекафе",
    "Kuhnya.ru": "Кухня", "NTVStyle.ru": "НТВ Стиль", "Nostalgia.ru": "Ностальгия", "Zhivi.ru": "Живи",
    "Sarafan.ru": "Сарафан", "360.ru": "Телеканал 360", "LapkiLive.ru": "Лапки LIVE", "TERRA.ru": "TERRA",
    "Vmirezhivotnykh.ru": "В мире животных", "ZvezdaPlus.ru": "Звезда Плюс", "Arsenal.ru": "Арсенал",
    "Doctor.ru": "Доктор", "BigPlanet.ru": "Big Planet", "MoyaPlaneta.ru": "Моя Планета", "Nauka.ru": "Наука",
    "DaVinci.ru": "Da Vinci", "vijuHistory.ru": "viju History", "Ohotnikirybolov.ru": "Охотник и рыболов",
    "Usadba.ru": "Усадьба", "Mama.ru": "Мама", "365daysTV.ru": "365 Дней", "Priklyucheniya.ru": "Приключения",
    "Istoriya.ru": "История", "RTDocumentary.ru@Russian": "Russia Today Doc", "T24.ru": "Т24",
    "vijuExplore.ru": "viju Explore", "vijuNature.ru": "viju Nature", "Bober.ru": "Бобер",
    "UnknownRussia.ru": "Неизвестная Россия", "RTGTV.ru": "RTG", "MatchBoets.ru": "Матч! Боец",
    "AutoPlus.ru": "Авто Плюс", "Avto24.ru": "Авто24", "MatchArena.ru": "Матч! Арена",
    "MatchIgra.ru": "Матч! Игра", "MMATVcom.ru": "MMA-TV.com", "BoksTV.ru": "Бокс ТВ",
    "MatchStrana.ru": "Матч! Страна", "RussianExtreme.ru": "Russian Extreme", "TiJi.ru": "TiJi", "Ani.ru": "Ani",
    "Multilandia.ru": "Мультиландия", "SuperGeroi.ru": "СуперГерои", "Detskiy.ru": "Детский", "Mult.ru": "Мульт",
    "GulliGirl.ru": "Gulli Girl", "O.ru": "О!", "RadostMoya.ru": "Радость моя", "Ryzhiy.ru": "Рыжий",
    "Multimuzyka.ru": "Мультимузыка", "RUTV.ru": "Ru.TV", "MuzykaLive.ru": "Музыка Live",
    "FONMusic.ru": "FON Music", "MuzykaPervogo.ru": "Музыка Первого", "BRIDGEClassic.ru": "Bridge TV Classic",
    "EuropaPlusTV.ru": "Europa Plus TV", "BRIDGE.ru": "Bridge TV", "ClassicMusic.ru": "Classic Music",
    "Izvestia.ru": "Известия", "RBKTV.ru": "РБК ТВ", "Mir24.ru": "Мир 24", "Maidan.ru": "Майдан",
    "TNVTatarstan.ru": "ТНВ", "Groznyj.ru": "Грозный", "TNVPlanet.ru": "ТНВ Планета", "SilkWay.kz": "Silk Way",
    "Belarus24.by": "Беларусь 24", "BST.ru": "БСТ", "BigAsia.ru": "Большая Азия",
    "IndiyskoyeKino.ru": "Индийское кино", "StartTriumf.ru": "Старт Триумф",
    "CartoonClassics.ru": "Cartoon Classic", "LDPRTV.ru": "ЛДПР ТВ", "vijuTV1000romantica.ru": "viju TV1000 romantica",
    "vijuTV1000novella.ru": "viju TV1000 новелла", "TV21.ru": "ТВ-21",
}
# Links that did not play from Russia when checked with `python build_playlist.py --check-from-here` on a
# home computer. GitHub's servers abroad cannot tell geo-blocking from a dead link, so this check runs at home.
DEAD_HERE = Path(__file__).with_name("dead_in_russia.txt")
# TV guide (EPG): free epg.one, Moscow time. The player matches channels by tvg-name; where epg.one spells a
# channel differently than CHANNELS, EPG_NAMES gives its spelling.
EPG_URL = "http://epg.one/epg.xml.gz"
EPG_NAMES = {
    "Пятница": "Пятница!", "Авто24": "Авто 24", "ТВ-21": "ТВ 21", "Bridge TV Classic": "Bridge Classic",
    "Cartoon Classic": "Cartoon Classics", "Star Family": "Star Family Россия HD", "TiJi": "Tiji TV",
    "365 Дней": "365 дней ТВ", "Старт Триумф": "Старт Триумф HD", "Ru.TV": "RU.TV", "Дом кино": "Дом Кино",
    "Индийское кино": "Индийское Кино", "Киноужас": "КИНОУЖАС", "START Air": "Start Air HD",
    "viju TV1000": "Viju TV1000", "viju TV1000 action": "Viju TV1000 Action",
    "viju TV1000 новелла": "Viju TV1000 Новелла HD", "viju Explore": "Viju Explore", "viju History": "Viju History",
    "Арсенал": "Арсенал HD", "Охотник и рыболов": "Охотник и рыболов HD", "Russia Today Doc": "Russia today doc",
    "RTG": "RTG TV", "8 канал": "Восьмой канал", "Ю": "Ю ТВ", "Большая Азия": "Большая Азия HD",
    "Приключения": "Приключения HD", "В мире животных": "В мире животных HD",
}
MERGED_VARIANTS = {"SD", "HD", ""}          # эти варианты одного канала склеиваются в один
CHECK_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
OUT = Path(__file__).with_name("playlist.m3u")

ATTR = re.compile(r'([\w-]+)="([^"]*)"')
RES = re.compile(r"\((\d+)[pi]\)")


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def parse(text):
    """Записи плейлиста: строка #EXTINF, строки #EXTVLCOPT и ссылка."""
    entries, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            # The name follows the first comma after the last quoted attribute: a user agent such as
            # "Mozilla/5.0 (KHTML, like Gecko)" has commas of its own.
            last_quote = line.rfind('"')
            comma = line.find(",", last_quote + 1)
            head, name = line[:comma], line[comma + 1:]
            attrs = dict(ATTR.findall(head))
            cur = {"attrs": attrs, "name": name.strip(), "opts": []}
        elif line.startswith("#EXTVLCOPT") and cur is not None:
            cur["opts"].append(line)
        elif line and not line.startswith("#") and cur is not None:
            cur["url"] = line
            entries.append(cur)
            cur = None
    return entries


def groups(entry):
    return {g.strip() for g in entry["attrs"].get("group-title", "").split(";") if g.strip()}


def category(entry):
    """Одна русская категория: Детские раньше Общих, всё незнакомое и Undefined — в Разное."""
    mapped = {CATEGORIES[g] for g in groups(entry) if g in CATEGORIES}
    for c in CATEGORY_ORDER:
        if c in mapped:
            return c
    return OTHER_CATEGORY


def channel_key(entry):
    """NTV.ru@SD и NTV.ru@HD дают один ключ NTV.ru; NTV.ru@Plus4 остаётся отдельным."""
    tvg = entry["attrs"].get("tvg-id", "")
    if not tvg:
        return "name:" + RES.sub("", entry["name"]).strip().lower()
    base, _, variant = tvg.partition("@")
    return base if variant in MERGED_VARIANTS else tvg


def resolution(entry):
    m = RES.search(entry["name"])
    return int(m.group(1)) if m else 0


def clean_name(entry):
    name = RES.sub("", entry["name"])
    name = re.sub(r"\[[^\]]*\]", "", name)          # [Geo-blocked], [Not 24/7]
    return re.sub(r"\s+", " ", name).strip()


def check(url):
    """'ok' — отвечает, 'dead' — явно мёртвая (нет DNS, 404, 410), 'unknown' — всё остальное."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as r:
            r.read(256)
            return "ok"
    except urllib.error.HTTPError as e:
        return "dead" if e.code in (404, 410) else "unknown"
    except urllib.error.URLError as e:
        return "dead" if isinstance(e.reason, socket.gaierror) else "unknown"
    except Exception:
        return "unknown"


def main():
    lists = [parse(download(u)) for u in SOURCES]
    russian = {channel_key(e) for e in lists[0]}
    channels = {}
    for i, entries in enumerate(lists):
        for e in entries:
            if groups(e) & EXCLUDED_GROUPS:
                continue
            key = channel_key(e)
            if i > 0 and key not in russian:       # из списка «Россия», но не на русском
                continue
            if key not in CHANNELS:                # только каналы из списка владельца
                continue
            channels.setdefault(key, []).append(e)

    urls = {e["url"] for es in channels.values() for e in es}
    with concurrent.futures.ThreadPoolExecutor(32) as pool:
        status = dict(zip(urls, pool.map(check, urls)))

    if "--check-from-here" in sys.argv:
        # Run at home in Russia: anything that does not answer here is recorded as not working.
        dead_here = sorted(u for u, s in status.items() if s != "ok")
        DEAD_HERE.write_text("\n".join(dead_here) + "\n", encoding="utf-8")
        print(f"не отвечают отсюда: {len(dead_here)} ссылок, записано в {DEAD_HERE.name}")
    if DEAD_HERE.exists():
        for u in DEAD_HERE.read_text(encoding="utf-8").split():
            if u in status:
                status[u] = "dead"

    rank = {"ok": 0, "unknown": 1}
    lines, kept_channels, kept_urls, dead = [f'#EXTM3U url-tvg="{EPG_URL}"'], 0, 0, 0
    def order(kv):
        best = max(kv[1], key=resolution)
        c = category(best)
        rank_c = CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER)
        return (rank_c, clean_name(best).lower())

    for key, entries in sorted(channels.items(), key=order):
        seen, alive = set(), []
        for e in entries:
            if e["url"] in seen:
                continue
            seen.add(e["url"])
            if status[e["url"]] == "dead":
                dead += 1
                continue
            alive.append(e)
        if not alive:
            continue
        alive.sort(key=lambda e: (rank[status[e["url"]]], -resolution(e)))
        main_entry = max(entries, key=resolution)    # имя, логотип и группа от лучшей версии
        attrs = main_entry["attrs"]
        tvg = key if not key.startswith("name:") else ""
        name = CHANNELS[key]
        for e in alive:
            extra = ""
            ua = e["attrs"].get("http-user-agent")
            if ua:
                extra += f' http-user-agent="{ua}"'
            ref = e["attrs"].get("http-referrer")
            if ref:
                extra += f' http-referrer="{ref}"'
            lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-name="{EPG_NAMES.get(name, name)}" tvg-logo="{attrs.get("tvg-logo", "")}"'
                         f'{extra} group-title="{category(main_entry)}",{name}')
            lines.extend(e["opts"])
            lines.append(e["url"])
            kept_urls += 1
        kept_channels += 1

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = sum(1 for s in status.values() if s == "ok")
    print(f"каналов: {kept_channels}, ссылок: {kept_urls} (отвечают сейчас: {ok}), убрано мёртвых: {dead}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
