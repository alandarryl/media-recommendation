#!/usr/bin/env python3
"""Recommandations quotidiennes (jeux, films, manga, anime, livres, kdrama, séries)
générées par Gemini et envoyées sur Discord (un salon par catégorie).

Aucune dépendance externe : uniquement la bibliothèque standard Python.

Variables d'environnement :
  GEMINI_API_KEY            (obligatoire) clé API Gemini
  DISCORD_WEBHOOK_<CAT>     un webhook par catégorie (voir CATEGORIES)
  GEMINI_MODEL              modèle Gemini (défaut : gemini-3.8-flash) ; accepte une liste
                            séparée par des virgules = modèles de secours, dans l'ordre
  DRY_RUN=true              affiche au lieu d'envoyer, n'écrit pas l'historique
  ONLY=jeux,films           ne traite que ces catégories (utile pour tester)
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY_FILE = ROOT / "history.json"

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
HISTORY_IN_PROMPT = 300   # nb de titres récents passés à l'IA pour éviter les doublons
PAUSE_BETWEEN_CATEGORIES = 8  # secondes (limite de requêtes/minute du palier gratuit)
GEMINI_TIMEOUT = 60           # secondes par appel (comme dans l'ancien projet)
MAX_RUNTIME = 600             # secondes : au-delà, les catégories restantes sont ignorées
# "0" = pas de réflexion (beaucoup plus rapide), "" = ne pas envoyer le paramètre
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "0").strip()

# --------------------------------------------------------------------------- #
# Configuration : catégories (= salons Discord) et emplacements (= les 9 reco)
# --------------------------------------------------------------------------- #

CATEGORIES = {
    "jeux": {
        "label": "Jeux vidéo", "emoji": "🎮", "webhook_env": "DISCORD_WEBHOOK_JEUX",
        "hint": "jeux vidéo (tous supports : PC, consoles, mobile, indé comme AAA)",
        "creator": "Studio / éditeur",
    },
    "films": {
        "label": "Films", "emoji": "🎬", "webhook_env": "DISCORD_WEBHOOK_FILMS",
        "hint": "films (tous pays, toutes époques, animation incluse)",
        "creator": "Réalisateur·rice",
    },
    "manga": {
        "label": "Manga", "emoji": "📚", "webhook_env": "DISCORD_WEBHOOK_MANGA",
        "hint": "mangas (y compris manhwa/manhua, one-shots et séries longues)",
        "creator": "Mangaka",
    },
    "anime": {
        "label": "Animé", "emoji": "🌸", "webhook_env": "DISCORD_WEBHOOK_ANIME",
        "hint": "séries et films d'animation japonaise (animés)",
        "creator": "Studio / réalisateur",
    },
    "livres": {
        "label": "Livres", "emoji": "📖", "webhook_env": "DISCORD_WEBHOOK_LIVRES",
        "hint": "livres : romans, fiction, non-fiction, psychologie, développement personnel, tous genres",
        "creator": "Auteur·rice",
    },
    "kdrama": {
        "label": "K-Drama", "emoji": "🇰🇷", "webhook_env": "DISCORD_WEBHOOK_KDRAMA",
        "hint": "séries télévisées coréennes (K-Drama)",
        "creator": "Réalisateur·rice / scénariste",
    },
    "series": {
        "label": "Séries", "emoji": "📺", "webhook_env": "DISCORD_WEBHOOK_SERIES",
        "hint": "séries TV et streaming (hors K-Drama et animés), tous pays",
        "creator": "Créateur·rice / showrunner",
    },
}

# L'ordre de cette liste = l'ordre d'affichage sur Discord.
# Pour passer à 8 (ou plus) : supprime ou ajoute simplement une ligne ici.
SLOTS = [
    {"key": "mystery", "label": "Mystère", "emoji": "🔍", "color": 0x6C5CE7,
     "brief": "genre mystère : intrigue, enquête, secrets, suspense à résoudre"},
    {"key": "romance", "label": "Romance", "emoji": "💕", "color": 0xFD79A8,
     "brief": "genre romance : une histoire d'amour est au cœur de l'œuvre"},
    {"key": "self_help", "label": "Développement personnel", "emoji": "🌱", "color": 0x00B894,
     "brief": "self-help : aide à grandir, à mieux se comprendre, à changer ses habitudes "
              "(pour un média de fiction, prends l'équivalent le plus proche : œuvre qui fait réfléchir "
              "sur soi, la résilience, l'apprentissage)"},
    {"key": "psychological", "label": "Psychologique", "emoji": "🧠", "color": 0x636E72,
     "brief": "genre psychologique : exploration de l'esprit, des émotions, de la perception, de la folie"},
    {"key": "hope", "label": "Espoir & inspiration", "emoji": "🌅", "color": 0xFDCB6E,
     "brief": "donne envie de continuer à vivre : espoir, résilience, inspirant, remonte le moral"},
    {"key": "cozy", "label": "Cozy & réconfort", "emoji": "☕", "color": 0xE17055,
     "brief": "cozy : doux, chaleureux, familier, rassurant, faibles enjeux, ambiance cocon"},
    {"key": "mainstream", "label": "Populaire · Incontournable", "emoji": "🔥", "color": 0xD63031,
     "brief": "grand succès ou référence incontournable du média ; AUCUNE contrainte de genre"},
    {"key": "niche", "label": "Niche & Culte", "emoji": "💎", "color": 0x0984E3,
     "brief": "peu connu du grand public mais adoré par une communauté précise (nomme-la dans 'why') ; "
              "AUCUNE contrainte de genre"},
    {"key": "wildcard", "label": "Wildcard · Curiosité", "emoji": "🎲", "color": 0x00CEC9,
     "brief": "pari singulier : une œuvre qui semble simplement intéressante à découvrir, "
              "pas pour sa notoriété ; AUCUNE contrainte de genre ni de popularité"},
]
SLOT_BY_KEY = {s["key"]: s for s in SLOTS}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "recommendations": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "slot": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "year": {"type": "INTEGER"},
                    "creator": {"type": "STRING"},
                    "synopsis": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
                "required": ["slot", "title", "year", "creator", "synopsis", "why"],
            },
        }
    },
    "required": ["recommendations"],
}


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Historique / anti-doublons
# --------------------------------------------------------------------------- #

def norm(title):
    """Normalise un titre pour comparer : sans accents, minuscules, sans ponctuation."""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"^(the|le|la|les|l|un|une|a|an)\s+", "", t.strip())
    return re.sub(r"[^a-z0-9]+", "", t)


def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("⚠️  history.json illisible, on repart d'un historique vide.")
    return {}


def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

def build_prompt(cat, slots, avoid):
    slot_lines = "\n".join(f'- "{s["key"]}" ({s["label"]}) : {s["brief"]}' for s in slots)
    avoid_txt = "\n".join(f"- {t}" for t in avoid) if avoid else "(aucun pour l'instant)"
    return f"""Tu es un curateur culturel expert, curieux, ouvert à tous les genres, époques et pays.

Médium : {cat["hint"]}.
Date du jour : {date.today().isoformat()}.

Propose exactement UNE recommandation pour chacun des emplacements suivants, dans cet ordre :
{slot_lines}

Règles strictes :
- Uniquement des œuvres RÉELLES et vérifiables. Si tu n'es pas sûr qu'une œuvre existe, choisis-en une autre.
- Le champ "slot" reprend exactement la clé de l'emplacement (ex. "mystery").
- "year" = année de sortie (entier), "creator" = {cat["creator"]}.
- "synopsis" : 1 à 2 phrases en français, sans spoiler (max 220 caractères).
- "why" : 1 phrase en français expliquant pourquoi c'est une bonne reco pour cet emplacement (max 180 caractères).
- Si un genre ne colle pas naturellement au médium, prends l'équivalent le plus proche sans forcer.
- Varie les époques, pays et tons d'un jour à l'autre ; ne te limite pas aux évidences.
- Pas deux fois la même œuvre ni la même franchise dans la réponse.
- N'utilise JAMAIS une œuvre de la liste ci-dessous (déjà recommandées) :
{avoid_txt}

Réponds uniquement avec le JSON demandé."""


def _gemini_body(prompt, with_thinking):
    gen = {
        "temperature": 1.0,
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA,
    }
    if with_thinking and GEMINI_THINKING_BUDGET != "":
        gen["thinkingConfig"] = {"thinkingBudget": int(GEMINI_THINKING_BUDGET)}
    return json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }).encode("utf-8")


def _call_model(model, prompt, api_key, retries=2):
    """Un modèle donné, avec quelques tentatives. Lève RuntimeError s'il échoue."""
    url = GEMINI_URL.format(model=model)
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    with_thinking = True

    last_err = None
    for attempt in range(retries):
        body = _gemini_body(prompt, with_thinking)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            return json.loads(text)["recommendations"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last_err = f"HTTP {e.code} (modèle : {model}): {detail}"
            if e.code == 400 and with_thinking and GEMINI_THINKING_BUDGET != "":
                # le modèle refuse le paramètre de réflexion : on réessaie sans
                log("   ⚠️  paramètre thinking refusé (400), nouvel essai sans.")
                with_thinking = False
                continue
            if e.code not in (429, 500, 502, 503, 504):
                break  # erreur non récupérable (clé invalide, modèle inconnu...)
        except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries - 1:
            wait = 5 * (2 ** attempt)
            log(f"   ↻ {model} a échoué ({' '.join(str(last_err).split())[:160]}). Nouvel essai dans {wait}s...")
            time.sleep(wait)
    raise RuntimeError(str(last_err))


_BAD_MODEL_WORDS = ("image", "tts", "live", "audio", "embedding", "robotics", "computer", "aqa", "veo", "imagen")
_model_order = None  # ordre des modèles à essayer ; mémorisé pour tout le run


def _discover_fallbacks(api_key, exclude):
    """Demande à Google la liste des modèles « flash » disponibles pour cette clé."""
    req = urllib.request.Request(
        GEMINI_URL.rsplit("/models/", 1)[0] + "/models?pageSize=200",
        headers={"x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            models = json.loads(resp.read().decode("utf-8")).get("models", [])
    except Exception as e:
        log(f"   ⚠️  liste des modèles indisponible ({type(e).__name__}).")
        return []
    names = []
    for m in models:
        name = m.get("name", "").removeprefix("models/")
        if ("flash" in name and "generateContent" in m.get("supportedGenerationMethods", [])
                and not any(w in name for w in _BAD_MODEL_WORDS) and name not in exclude):
            names.append(name)
    return sorted(names, reverse=True)[:3]  # les plus récents d'abord


def call_gemini(prompt, api_key):
    """Essaie les modèles dans l'ordre ; bascule sur un autre si le premier est saturé."""
    global _model_order
    if _model_order is None:
        _model_order = [m.strip() for m in GEMINI_MODEL.split(",") if m.strip()]
    errors, discovered = [], False
    i = 0
    while i < len(_model_order):
        model = _model_order[i]
        try:
            result = _call_model(model, prompt, api_key)
            if i > 0:  # on garde le modèle qui marche pour la suite du run
                _model_order.insert(0, _model_order.pop(i))
                log(f"   ➜ bascule sur le modèle {model} pour la suite.")
            return result
        except RuntimeError as e:
            errors.append(str(e))
            log(f"   ✗ {model} indisponible.")
        i += 1
        if i >= len(_model_order) and not discovered:
            discovered = True
            _model_order.extend(_discover_fallbacks(api_key, set(_model_order)))
    raise RuntimeError("Gemini inaccessible : " + " | ".join(' '.join(e.split())[:200] for e in errors))


def generate_for_category(cat, history_titles, known_norms, api_key):
    """Retourne la liste des reco (dans l'ordre de SLOTS), sans doublons."""
    chosen = {}
    seen = set(known_norms)

    for attempt in (1, 2):  # 2e passage : on ne redemande que les emplacements manquants
        missing = [s for s in SLOTS if s["key"] not in chosen]
        if not missing:
            break
        avoid = list(history_titles[-HISTORY_IN_PROMPT:]) + [c["title"] for c in chosen.values()]
        items = call_gemini(build_prompt(cat, missing, avoid), api_key)
        wanted = {s["key"] for s in missing}
        for it in items:
            if not isinstance(it, dict):
                continue
            slot, title = it.get("slot"), str(it.get("title", "")).strip()
            if slot not in wanted or slot in chosen or not title:
                continue
            n = norm(title)
            if not n or n in seen:
                log(f"   ⛔ doublon écarté : {title}")
                continue
            seen.add(n)
            chosen[slot] = {
                "slot": slot,
                "title": title,
                "year": it.get("year"),
                "creator": str(it.get("creator", "")).strip(),
                "synopsis": str(it.get("synopsis", "")).strip(),
                "why": str(it.get("why", "")).strip(),
            }
        if attempt == 1 and len(chosen) < len(SLOTS):
            log(f"   … {len(SLOTS) - len(chosen)} emplacement(s) à compléter")

    return [chosen[s["key"]] for s in SLOTS if s["key"] in chosen]


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #

def cut(text, n):
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build_embed(item):
    slot = SLOT_BY_KEY[item["slot"]]
    year = f" ({item['year']})" if item.get("year") else ""
    lines = [f"**{cut(item['title'], 150)}**{year}"]
    if item["creator"]:
        lines.append(f"*{cut(item['creator'], 100)}*")
    if item["synopsis"]:
        lines.append("\n" + cut(item["synopsis"], 260))
    if item["why"]:
        lines.append("\n💡 " + cut(item["why"], 200))
    return {
        "title": f"{slot['emoji']} {slot['label']}",
        "description": "\n".join(lines),
        "color": slot["color"],
    }


def embed_size(e):
    return len(e.get("title", "")) + len(e.get("description", ""))


def chunk_embeds(embeds, max_chars=5500, max_count=10):
    """Découpe pour respecter les limites Discord (6000 caractères / 10 embeds par message)."""
    chunks, cur, size = [], [], 0
    for e in embeds:
        s = embed_size(e)
        if cur and (size + s > max_chars or len(cur) >= max_count):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += s
    if cur:
        chunks.append(cur)
    return chunks


def post_webhook(url, payload, retries=4):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "DiscordBot (reco-bot, 1.0)"}
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429:
                try:
                    wait = float(json.loads(body).get("retry_after", 2))
                except (ValueError, json.JSONDecodeError):
                    wait = 2.0
                time.sleep(wait + 0.5)
                continue
            raise RuntimeError(f"Discord HTTP {e.code}: {body[:300]}")
        except urllib.error.URLError as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Discord injoignable : {e}")
            time.sleep(3)
    raise RuntimeError("Discord : trop de tentatives (rate limit)")


def send_to_discord(webhook, cat, picks):
    header = f"{cat['emoji']} **{cat['label']} · sélection du {date.today().strftime('%d/%m/%Y')}**"
    chunks = chunk_embeds([build_embed(p) for p in picks])
    for i, chunk in enumerate(chunks):
        payload = {"username": "Reco du jour", "embeds": chunk}
        if i == 0:
            payload["content"] = header
        post_webhook(webhook, payload)
        time.sleep(1)


def print_dry(cat, picks):
    log(f"\n=== {cat['emoji']} {cat['label']} ===")
    for p in picks:
        slot = SLOT_BY_KEY[p["slot"]]
        log(f" {slot['emoji']} [{slot['label']}] {p['title']} ({p['year']}) - {p['creator']}")
        log(f"     {p['synopsis']}")
        log(f"     💡 {p['why']}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    dry = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    only = {x.strip() for x in os.getenv("ONLY", "").split(",") if x.strip()}
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        sys.exit("❌ GEMINI_API_KEY manquante.")

    unknown = only - set(CATEGORIES)
    if unknown:
        sys.exit(f"❌ Catégories inconnues dans ONLY : {', '.join(sorted(unknown))}")

    history = load_history()
    failures = 0
    todo = [(k, c) for k, c in CATEGORIES.items() if not only or k in only]

    started = time.monotonic()
    for idx, (key, cat) in enumerate(todo):
        if time.monotonic() - started > MAX_RUNTIME:
            log(f"\n⏱️  Durée max ({MAX_RUNTIME}s) dépassée : catégories restantes ignorées.")
            failures += len(todo) - idx
            break
        log(f"\n▶ {cat['label']}")
        webhook = os.getenv(cat["webhook_env"], "").strip()
        if not webhook and not dry:
            log(f"   ⚠️  {cat['webhook_env']} non défini : catégorie ignorée.")
            continue
        try:
            entries = history.setdefault(key, [])
            titles = [e["title"] for e in entries]
            known = {norm(t) for t in titles}
            picks = generate_for_category(cat, titles, known, api_key)
            if not picks:
                raise RuntimeError("aucune recommandation exploitable")
            if len(picks) < len(SLOTS):
                log(f"   ⚠️  seulement {len(picks)}/{len(SLOTS)} recommandations valides")

            if dry:
                print_dry(cat, picks)
            else:
                send_to_discord(webhook, cat, picks)
                today = date.today().isoformat()
                entries.extend({"title": p["title"], "year": p["year"], "date": today} for p in picks)
                save_history(history)  # sauvegarde après chaque catégorie réussie
                log(f"   ✅ {len(picks)} recommandations envoyées")
        except Exception as e:  # une catégorie qui échoue ne bloque pas les autres
            failures += 1
            log(f"   ❌ {cat['label']} : {e}")

        if idx < len(todo) - 1:
            time.sleep(PAUSE_BETWEEN_CATEGORIES)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
