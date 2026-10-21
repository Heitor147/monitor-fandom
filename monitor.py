#!/usr/bin/env python3
"""
Fandom Watcher
Monitora edições em páginas específicas e atividade no Message Wall de um
perfil da Fandom, e manda notificação por email + WhatsApp (CallMeBot)
quando algo novo acontece.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests
import ai_summary

HEADERS = {"User-Agent": "fandom-watcher/1.0 (monitor pessoal de wiki)"}

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"

# ---------- infraestrutura básica ----------

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def api_url(wiki):
    return f"https://{wiki}.fandom.com/api.php"


def wikia_url(wiki):
    return f"https://{wiki}.fandom.com/wikia.php"


def normalize_username(username):
    return " ".join(str(username).replace("_", " ").split()).casefold()


# ---------- config e estado por wiki ----------

def normalize_config(config):
    """Devolve {wiki: {"pages": [...], "wall": bool}}.

    Aceita 3 formatos:
    - novo:    {"wikis": {"nome-da-wiki": {"pages": [...], "wall": true}}}
    - antigo:  {"wiki": "nome", "pages": [...]}
    - lista:   {"wiki": ["a", "b"], "pages": [...]}  (as páginas valem pra todas;
               numa wiki onde a página não existe, ela é simplesmente ignorada)
    """
    has_profile = bool(config.get("profile"))
    if "wikis" in config:
        return {
            name: {"pages": opts.get("pages", []), "wall": opts.get("wall", has_profile)}
            for name, opts in config["wikis"].items()
        }
    wikis = config.get("wiki", [])
    if isinstance(wikis, str):
        wikis = [wikis]
    pages = config.get("pages", [])
    return {name: {"pages": pages, "wall": has_profile} for name in wikis}


def migrate_state(state, wikis):
    """Estado antigo (sem separação por wiki) pertencia à camp-halfblood-fanon."""
    if "wikis" in state:
        return state
    new = {"wikis": {}}
    legacy = {k: state[k] for k in ("pages", "wall_threads") if k in state}
    if legacy and "camp-halfblood-fanon" in wikis:
        new["wikis"]["camp-halfblood-fanon"] = legacy
    return new


# ---------- checagem de edições ----------

def get_latest_revisions(wiki, title, limit=5):
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": title,
        "rvlimit": limit,
        "rvprop": "timestamp|user|comment|ids",
        "format": "json",
    }
    r = requests.get(api_url(wiki), params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None, []
    page = next(iter(pages.values()))
    pageid = page.get("pageid")
    revisions = page.get("revisions", [])
    return pageid, revisions


def check_pages(wiki, titles, state, events, ignored_users):
    state.setdefault("pages", {})
    for title in titles:
        pageid, revisions = get_latest_revisions(wiki, title)
        if pageid is None or not revisions:
            continue
        key = str(pageid)
        last_known = state["pages"].get(key, {}).get("last_revid")
        # revisions vêm em ordem decrescente (mais nova primeiro)
        new_revisions = []
        for rev in revisions:
            if last_known is not None and rev["revid"] <= last_known:
                break
            new_revisions.append(rev)

        if last_known is not None:
            for rev in reversed(new_revisions):
                if normalize_username(rev.get("user", "")) in ignored_users:
                    continue
                qtitle = quote(title, safe="/()_'")
                page_link = f"https://{wiki}.fandom.com/wiki/{qtitle}"
                revert_link = (
                    f"https://{wiki}.fandom.com/index.php?title={qtitle}"
                    f"&action=edit&undoafter={rev.get('parentid', 0)}&undo={rev['revid']}"
                )
                events.append({
                    "type": "edit",
                    "wiki": wiki,
                    "page": title.replace("_", " "),
                    "user": rev.get("user", "desconhecido"),
                    "comment": rev.get("comment") or "(sem resumo)",
                    "timestamp": rev.get("timestamp"),
                    "accept_link": page_link,
                    "revert_link": revert_link,
                })

        if revisions:
            state["pages"][key] = {"last_revid": revisions[0]["revid"], "title": title}


# ---------- checagem do mural (message wall) ----------

def get_wall_threads(wiki, wall_owner_id, limit=15):
    params = {
        "controller": r"Fandom\MessageWall\MessageWall",
        "method": "getThreads",
        "format": "json",
        "sortDirection": "descending",
        "sortBy": "creation_date",
        "limit": limit,
        "wallOwnerId": wall_owner_id,
    }
    r = requests.get(wikia_url(wiki), params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("_embedded", {}).get("threads", [])


def check_wall(wiki, username, wall_owner_id, state, events, ignored_users):
    # Primeira vez nessa wiki: só grava o baseline, sem notificar threads que já existiam
    baseline = "wall_threads" not in state
    state.setdefault("wall_threads", {})
    threads = get_wall_threads(wiki, wall_owner_id)

    for thread in threads:
        tid = thread["id"]
        known = state["wall_threads"].get(tid)
        thread_link = f"https://{wiki}.fandom.com/wiki/Message_Wall:{quote(username)}?threadId={tid}"

        post_count = thread.get("postCount", 0)
        upvotes = thread.get("upvoteCount", 0)
        author = thread.get("createdBy", {}).get("name", "desconhecido")
        title = thread.get("title") or "(sem título)"
        snippet = (thread.get("rawContent") or "")[:200]

        if baseline:
            pass
        elif known is None:
            if normalize_username(author) not in ignored_users:
                events.append({
                    "type": "wall_new",
                    "wiki": wiki,
                    "title": title,
                    "author": author,
                    "snippet": snippet,
                    "link": thread_link,
                })
        else:
            if post_count > known.get("post_count", 0):
                events.append({
                    "type": "wall_reply",
                    "wiki": wiki,
                    "title": title,
                    "author": author,
                    "snippet": snippet,
                    "link": thread_link,
                })
            if upvotes > known.get("upvote_count", 0):
                events.append({
                    "type": "wall_like",
                    "wiki": wiki,
                    "title": title,
                    "count": upvotes,
                    "link": thread_link,
                })

        state["wall_threads"][tid] = {
            "post_count": post_count,
            "upvote_count": upvotes,
        }


# ---------- formatação das notificações ----------

def format_event(ev):
    if ev["type"] == "edit":
        return (
            f"✏️ [{ev['wiki']}] Edição em \"{ev['page']}\"\n"
            f"Por: {ev['user']}\n"
            f"Resumo: {ev.get('ai') or ev['comment']}\n"
            f"Quando: {ev['timestamp']}\n"
            + (f"IA: {ev['ai']}\n" if ev.get("ai") else "")
            + f"Aceitar (ver página): {ev['accept_link']}\n"
            f"Reverter (abre editor): {ev['revert_link']}"
        )
    if ev["type"] == "wall_new":
        return (
            f"💬 [{ev['wiki']}] Novo post no mural: \"{ev['title']}\"\n"
            f"De: {ev['author']}\n"
            f"Trecho: {ev['snippet']}\n"
            f"Ver: {ev['link']}"
        )
    if ev["type"] == "wall_reply":
        return (
            f"↩️ [{ev['wiki']}] Nova resposta em \"{ev['title']}\"\n"
            f"De: {ev['author']}\n"
            f"Trecho: {ev['snippet']}\n"
            f"Ver: {ev['link']}"
        )
    if ev["type"] == "wall_like":
        return (
            f"👍 [{ev['wiki']}] Curtida em \"{ev['title']}\" (total: {ev['count']})\n"
            f"Ver: {ev['link']}"
        )
    return str(ev)


# ---------- envio ----------

def send_email(subject, body):
    """Retorna True se enviou, False se falhou, None se não está configurado."""
    api_key = os.environ.get("RESEND_API_KEY")
    to_addr = os.environ.get("NOTIFY_EMAIL")

    if not all([api_key, to_addr]):
        print("Email não configurado (faltando RESEND_API_KEY ou NOTIFY_EMAIL), pulando.")
        return None

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                # onboarding@resend.dev funciona sem precisar verificar domínio próprio
                "from": "Fandom Watcher <onboarding@resend.dev>",
                "to": [to_addr],
                "subject": subject,
                "text": body,
            },
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"::error::Falha de rede ao mandar email: {e}")
        return False

    if r.status_code >= 300:
        print(f"::error::Erro ao mandar email: {r.status_code} {r.text}")
        return False
    return True


def send_whatsapp(text):
    """Retorna True se enviou, False se falhou, None se não está configurado."""
    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")

    if not all([phone, apikey]):
        print("WhatsApp (CallMeBot) não configurado, pulando.")
        return None

    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": text, "apikey": apikey},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"::error::Falha de rede ao mandar WhatsApp: {e}")
        return False

    if r.status_code != 200:
        print(f"::error::Erro ao mandar WhatsApp: {r.status_code} {r.text[:200]}")
        return False
    return True


# ---------- main ----------

def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("::error::config.json não encontrado.")
        sys.exit(1)

    wikis = normalize_config(config)
    state = migrate_state(load_json(STATE_PATH, {}), wikis)
    events = []

    profile = config.get("profile")
    ignored_users = {
        normalize_username(user) for user in config.get("ignored_users", [])
    }

    # Cada wiki (e cada parte dela) é isolada: uma wiki fora do ar vira aviso, não derruba as outras
    for wiki, opts in wikis.items():
        wstate = state["wikis"].setdefault(wiki, {})
        try:
            check_pages(wiki, opts["pages"], wstate, events, ignored_users)
        except Exception as e:
            print(f"::warning::[{wiki}] Falha ao checar páginas: {type(e).__name__}: {e}")

        if profile and opts["wall"]:
            try:
                check_wall(
                    wiki,
                    profile["username"],
                    profile["user_id"],
                    wstate,
                    events,
                    ignored_users,
                )
            except Exception as e:
                print(f"::warning::[{wiki}] Falha ao checar o mural: {type(e).__name__}: {e}")

    if not events:
        save_json(STATE_PATH, state)
        print("Nada novo.")
        return

    print(f"{len(events)} evento(s) novo(s).")
    ai_summary.enrich(events)

    results = []
    body = "\n\n".join(format_event(ev) for ev in events)
    results.append(send_email(f"Fandom Watcher: {len(events)} novidade(s)", body))

    # WhatsApp: um resumo curto por evento (CallMeBot tem limite de tamanho)
    for ev in events:
        results.append(send_whatsapp(format_event(ev)[:600]))

    if any(r is True for r in results):
        save_json(STATE_PATH, state)
    else:
        # Nenhum canal entregou: não salva o estado, assim o evento é detectado de novo no próximo ciclo
        print("::error::Nenhuma notificação foi entregue. Estado NÃO salvo, vai tentar de novo.")
        sys.exit(1)


if __name__ == "__main__":
    main()
