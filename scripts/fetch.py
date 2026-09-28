"""Descarga subtítulos en español para el catálogo de HourTV.

Lee el catálogo publicado (Supabase, clave anónima), busca cada película y
temporada en SubDL por tmdb_id y guarda:

  movie/<tmdb>.es.srt
  tv/<id del título en el catálogo>/S01E02.es.srt

Las series se guardan por id del catálogo: muchas no tienen tmdb_id y cada
temporada suele ser un título aparte ("Loki - Temporada 2 (2023)").

La app los lee directo de raw.githubusercontent.com. Solo usa la biblioteca
estándar de Python. Variables: SUBDL_API_KEY, SUPABASE_URL, SUPABASE_ANON_KEY.
"""

import datetime
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile

API = "https://api.subdl.com/api/v1/subtitles"
# Búsqueda en TMDB (en español) del panel de HourTV: las series del catálogo
# no traen tmdb_id y su nombre está en español.
TMDB_SEARCH = os.environ.get("TMDB_SEARCH_URL", "https://hourtv-adming.vercel.app/api/tmdb")
DL = "https://dl.subdl.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state.json")
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES", 1500))  # el plan gratis da 2000/día
RETRY_DAYS = 7  # lo que no se encontró se vuelve a buscar a la semana
TIMESTAMP = re.compile(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")
EPISODE = re.compile(r"(?i)s(\d{1,2})[ ._-]*e(\d{1,3})|(\d{1,2})x(\d{2,3})")

searches = 0


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "HourTV-Subtitles/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def catalog(table, select):
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_ANON_KEY"]
    rows, start = [], 0
    while True:
        url = f"{base}/rest/v1/{table}?select={select}&deleted_at=is.null&order=id"
        page = json.loads(get(url, {"apikey": key, "Authorization": f"Bearer {key}", "Range": f"{start}-{start + 999}"}))
        rows += page
        if len(page) < 1000:
            return rows
        start += 1000


def search(**params):
    global searches
    searches += 1
    time.sleep(1)  # sin apuro: no saturar la API
    results, page = [], 1
    while True:
        query = {"api_key": os.environ["SUBDL_API_KEY"], "languages": "ES", "subs_per_page": 30, "page": page, **params}
        data = json.loads(get(f"{API}?{urllib.parse.urlencode(query)}"))
        if not data.get("status"):
            return results
        results += data.get("subtitles") or []
        if page >= int(data.get("totalPages") or 1) or page >= 3:
            return results
        page += 1


def srt_files(sub):
    """Archivos .srt del zip de un subtítulo, como (nombre, texto utf-8)."""
    path = urllib.parse.urlparse(sub["url"]).path
    time.sleep(0.5)
    data = get(DL + path)
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if not name.lower().endswith(".srt"):
                continue
            raw = z.read(name)
            for enc in ("utf-8-sig", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if TIMESTAMP.search(text):
                out.append((name, text))
    return out


def ranked(subs):
    # Primero los que no son para sordos (sin [música], (risas)...).
    return sorted(subs, key=lambda s: bool(s.get("hi")))


def write(rel, text):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def fetch_movie(tmdb):
    for sub in ranked(search(tmdb_id=tmdb, type="movie"))[:4]:
        try:
            files = srt_files(sub)
        except Exception as e:  # zip roto o caído: probar el siguiente
            print(f"  zip error {e}")
            continue
        # Varios .srt suele ser CD1/CD2: se descarta, no sirve partido.
        if len(files) == 1:
            write(f"movie/{tmdb}.es.srt", files[0][1])
            return True
    return False


SEASON_IN_NAME = re.compile(r"\s*[-–:]?\s*temporada\s*(\d+).*$", re.I)


def series_query(title, db_season, seasons_in_title):
    """(parámetros de búsqueda en SubDL, temporada real) de una temporada."""
    name = title["title"].split("|")[0].strip()
    m = SEASON_IN_NAME.search(name)
    real = db_season
    if m:
        name = name[: m.start()].strip()
        # "Loki - Temporada 2" con una sola temporada cargada: es la 2.
        if seasons_in_title == 1:
            real = int(m.group(1))
    year = re.search(r"\((\d{4})\)", title["title"])
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
    tmdb = title.get("tmdb_id") or tmdb_tv_id(name, int(year.group(1)) if year else None)
    return ({"tmdb_id": tmdb} if tmdb else None), real


def norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tmdb_tv_id(name, year):
    """TMDB id de la serie [name]. [year] es el de la temporada: se elige la
    serie más reciente que ya existía ese año (Avatar 2024, no la de 2005)."""
    data = json.loads(get(f"{TMDB_SEARCH}?action=search&q={urllib.parse.quote(name)}"))
    shows = [r for r in data.get("results", []) if r.get("tmdbType") == "tv"]
    exact = [r for r in shows if norm(name) in (norm(r["title"]), norm(r["originalTitle"]))]
    candidates = exact or shows[:1]
    if year:
        before = [r for r in candidates if r.get("year") and r["year"] <= year]
        candidates = sorted(before, key=lambda r: r["year"], reverse=True) or candidates
    return candidates[0]["tmdbId"] if candidates else None


def fetch_season(query, season, wanted, out_dir, db_season):
    """Guarda los episodios [wanted] de una temporada; devuelve los que faltan."""
    missing = set(wanted)
    for sub in ranked(search(type="tv", season_number=season, **query)):
        if not missing:
            break
        if sub.get("season") not in (None, season):
            continue
        single = sub.get("episode")
        if single and single not in missing and not sub.get("full_season"):
            continue
        try:
            files = srt_files(sub)
        except Exception as e:
            print(f"  zip error {e}")
            continue
        for name, text in files:
            m = EPISODE.search(os.path.basename(name))
            if m:
                s = int(m.group(1) or m.group(3))
                ep = int(m.group(2) or m.group(4))
            elif single and len(files) == 1:
                s, ep = season, single
            else:
                continue
            if s == season and ep in missing:
                write(f"{out_dir}/S{db_season:02d}E{ep:02d}.es.srt", text)
                missing.discard(ep)
    return missing


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = datetime.date.today()

    def due(key):
        entry = state.get(key)
        if entry is None:
            return True
        if entry["ok"]:
            return False
        return (today - datetime.date.fromisoformat(entry["t"])).days >= RETRY_DAYS

    titles = catalog("titles", "id,tmdb_id,media_type,title")
    seasons = catalog("seasons", "id,title_id,season_number")
    episodes = catalog("episodes", "season_id,episode_number")
    by_id = {t["id"]: t for t in titles}

    jobs = [("movie", t, None) for t in titles if t["media_type"] == "movie" and t.get("tmdb_id")]
    per_title = {}
    for s in seasons:
        per_title[s["title_id"]] = per_title.get(s["title_id"], 0) + 1
    for s in seasons:
        t = by_id.get(s["title_id"])
        if t and t["media_type"] == "series":
            eps = sorted({e["episode_number"] for e in episodes if e["season_id"] == s["id"]})
            if eps:
                jobs.append(("tv", t, (s["season_number"], eps, per_title[s["title_id"]])))

    found = 0
    for kind, t, season in jobs:
        if searches >= MAX_SEARCHES:
            print("límite diario alcanzado; sigue mañana")
            break
        tmdb = t.get("tmdb_id")
        try:
            if kind == "movie":
                key = f"movie/{tmdb}"
                if not due(key):
                    continue
                ok = fetch_movie(tmdb)
            else:
                number, eps, count = season
                out_dir = f"tv/{t['id']}"
                key = f"{out_dir}/S{number:02d}"
                eps = [e for e in eps if not os.path.exists(os.path.join(ROOT, f"{out_dir}/S{number:02d}E{e:02d}.es.srt"))]
                entry = state.get(key)
                # Una temporada completa vuelve a buscarse si el catálogo sumó
                # episodios; una incompleta, cada RETRY_DAYS.
                if not eps or (entry and not entry["ok"] and not due(key)):
                    continue
                query, real = series_query(t, number, count)
                ok = bool(query) and not fetch_season(query, real, eps, out_dir, number)
        except Exception as e:  # la API falló con este título: se reintenta otro día
            print(f"{t['title']}: error {e}")
            continue
        state[key] = {"ok": ok, "t": today.isoformat()}
        found += ok
        print(f"{'OK ' if ok else '-- '}{key} {t['title']}")
        with open(STATE, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)

    print(f"búsquedas: {searches}, completos: {found}")


if __name__ == "__main__":
    sys.exit(main())
