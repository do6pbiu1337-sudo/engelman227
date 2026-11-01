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
            channels.setdefault(key, []).append(e)

    urls = {e["url"] for es in channels.values() for e in es}
    with concurrent.futures.ThreadPoolExecutor(32) as pool:
        status = dict(zip(urls, pool.map(check, urls)))

    rank = {"ok": 0, "unknown": 1}
    lines, kept_channels, kept_urls, dead = ["#EXTM3U"], 0, 0, 0
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
        name = clean_name(main_entry)
        for e in alive:
            extra = ""
            ua = e["attrs"].get("http-user-agent")
            if ua:
                extra += f' http-user-agent="{ua}"'
            ref = e["attrs"].get("http-referrer")
            if ref:
                extra += f' http-referrer="{ref}"'
            lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{attrs.get("tvg-logo", "")}"'
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
