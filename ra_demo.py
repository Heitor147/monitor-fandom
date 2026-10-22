"""Roda o Ra numa edição REAL já existente, sem esperar alguém editar suas páginas.

Uso (PowerShell, na pasta do projeto):
    $env:GEMINI_API_KEY = "sua-chave"
    python ra_demo.py camp-halfblood-fanon "Hera_(The_One_Who_Theorizes)"

Pega a edição mais recente da página feita por OUTRA pessoa (as suas ficam de fora).
Se não houver nenhuma, use --any pra aceitar qualquer autor (serve só pra testar).
Não envia email nem WhatsApp, só imprime os dois formatos de aviso.
"""
import os
import sys
from urllib.parse import quote

import monitor
import ra


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    accept_any = "--any" in sys.argv
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    if not os.environ.get("GEMINI_API_KEY"):
        print("Defina GEMINI_API_KEY antes (veja o topo do arquivo).")
        sys.exit(1)

    wiki, title = args
    config = monitor.load_json(monitor.CONFIG_PATH, None)
    wikis = monitor.normalize_config(config)
    ignored = {monitor.normalize_username(u) for u in config.get("ignored_users", [])}

    _, revisions = monitor.get_latest_revisions(wiki, title, limit=30)
    rev = next(
        (r for r in revisions
         if r.get("parentid") and (accept_any or monitor.normalize_username(r.get("user", "")) not in ignored)),
        None,
    )
    if rev is None:
        print("Nenhuma edição de outra pessoa nas últimas 30 revisões. Tente --any.")
        sys.exit(1)

    qtitle = quote(title, safe="/()_'")
    ev = {
        "type": "edit",
        "wiki": wiki,
        "page": title.replace("_", " "),
        "user": rev.get("user", "desconhecido"),
        "comment": rev.get("comment") or "(sem resumo)",
        "timestamp": rev.get("timestamp"),
        "accept_link": f"https://{wiki}.fandom.com/wiki/{qtitle}",
        "revert_link": (f"https://{wiki}.fandom.com/index.php?title={qtitle}"
                        f"&action=edit&undoafter={rev['parentid']}&undo={rev['revid']}"),
    }
    print(f"Edição escolhida: {ev['user']} em {ev['timestamp']} (rev {rev['revid']})\n")

    ra.enrich([ev], wikis)
    if "ai" not in ev:
        print("O Ra não devolveu nada (sem diff ou erro acima).")
        return
    print("=== EMAIL ===")
    print(monitor.format_event(ev, full=True))
    print("\n=== WHATSAPP ===")
    wa = monitor.format_event(ev, full=False)[:600]
    print(wa)
    print(f"\n({len(wa)} caracteres)")


if __name__ == "__main__":
    main()
