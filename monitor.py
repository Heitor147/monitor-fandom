#!/usr/bin/env python3
"""
Fandom Watcher
Monitora edições em páginas específicas e atividade no Message Wall de um
perfil da Fandom, e manda notificação por email + WhatsApp (CallMeBot)
quando algo novo acontece.
"""

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests

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
    r = requests.get(api_url(wiki), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None, []
    page = next(iter(pages.values()))
    pageid = page.get("pageid")
    revisions = page.get("revisions", [])
    return pageid, revisions


def check_pages(wiki, titles, state, events):
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
                page_link = f"https://{wiki}.fandom.com/wiki/{title}"
                revert_link = (
                    f"https://{wiki}.fandom.com/index.php?title={title}"
                    f"&action=edit&undoafter={rev['parentid']}&undo={rev['revid']}"
                )
                events.append({
                    "type": "edit",
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
    r = requests.get(wikia_url(wiki), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("_embedded", {}).get("threads", [])


def check_wall(wiki, username, wall_owner_id, state, events):
    state.setdefault("wall_threads", {})
    threads = get_wall_threads(wiki, wall_owner_id)

    for thread in threads:
        tid = thread["id"]
        known = state["wall_threads"].get(tid)
        thread_link = f"https://{wiki}.fandom.com/wiki/Message_Wall:{username}?threadId={tid}"

        post_count = thread.get("postCount", 0)
        upvotes = thread.get("upvoteCount", 0)
        author = thread.get("createdBy", {}).get("name", "desconhecido")
        title = thread.get("title") or "(sem título)"
        snippet = (thread.get("rawContent") or "")[:200]

        if known is None:
            events.append({
                "type": "wall_new",
                "title": title,
                "author": author,
                "snippet": snippet,
                "link": thread_link,
            })
        else:
            if post_count > known.get("post_count", 0):
                events.append({
                    "type": "wall_reply",
                    "title": title,
                    "author": author,
                    "snippet": snippet,
                    "link": thread_link,
                })
            if upvotes > known.get("upvote_count", 0):
                events.append({
                    "type": "wall_like",
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
            f"✏️ Edição em \"{ev['page']}\"\n"
            f"Por: {ev['user']}\n"
            f"Resumo: {ev['comment']}\n"
            f"Quando: {ev['timestamp']}\n"
            f"Aceitar (ver página): {ev['accept_link']}\n"
            f"Reverter (abre editor): {ev['revert_link']}"
        )
    if ev["type"] == "wall_new":
        return (
            f"💬 Novo post no mural: \"{ev['title']}\"\n"
            f"De: {ev['author']}\n"
            f"Trecho: {ev['snippet']}\n"
            f"Ver: {ev['link']}"
        )
    if ev["type"] == "wall_reply":
        return (
            f"↩️ Nova resposta em \"{ev['title']}\"\n"
            f"De: {ev['author']}\n"
            f"Trecho: {ev['snippet']}\n"
            f"Ver: {ev['link']}"
        )
    if ev["type"] == "wall_like":
        return (
            f"👍 Curtida em \"{ev['title']}\" (total: {ev['count']})\n"
            f"Ver: {ev['link']}"
        )
    return str(ev)


# ---------- envio ----------

def send_email(subject, body):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("NOTIFY_EMAIL")

    if not all([host, user, password, to_addr]):
        print("Email não configurado (faltando variáveis de ambiente), pulando.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())


def send_whatsapp(text):
    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")

    if not all([phone, apikey]):
        print("WhatsApp (CallMeBot) não configurado, pulando.")
        return

    params = {"phone": phone, "text": text, "apikey": apikey}
    r = requests.get("https://api.callmebot.com/whatsapp.php", params=params, timeout=20)
    if r.status_code != 200:
        print(f"Erro ao mandar WhatsApp: {r.status_code} {r.text}")


# ---------- main ----------

def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("config.json não encontrado.")
        sys.exit(1)

    state = load_json(STATE_PATH, {})
    events = []

    wiki = config["wiki"]
    check_pages(wiki, config.get("pages", []), state, events)

    profile = config.get("profile")
    if profile:
        check_wall(wiki, profile["username"], profile["user_id"], state, events)

    save_json(STATE_PATH, state)

    if not events:
        print("Nada novo.")
        return

    print(f"{len(events)} evento(s) novo(s).")

    body = "\n\n".join(format_event(ev) for ev in events)
    send_email(f"Fandom Watcher: {len(events)} novidade(s)", body)

    # WhatsApp: manda um resumo curto por evento (CallMeBot tem limite de tamanho)
    for ev in events:
        send_whatsapp(format_event(ev)[:600])


if __name__ == "__main__":
    main()
