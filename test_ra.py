"""Testes offline do Ra (sem internet, sem Gemini). Rode: python test_ra.py"""
import os
import unittest
from unittest import mock

import ai_summary
import monitor
import ra

PAGES = {
    "Hera (Eu)": "'''Hera''' é a rainha dos deuses.\nEla aparenta 17 anos.\nSeu cabelo é prateado.\n[[Category:Deuses]]",
    "Zeus (Eu)": "[[Zeus]] é irmão de Hera e governa o Olimpo.\nZeus perdeu o raio em 1990.\n<ref>nota</ref>",
    "Poseidon (Eu)": "Poseidon vive no mar e odeia gatos.\nSua cidade é Atlântida.",
}
OLD_HERA = PAGES["Hera (Eu)"]
DIFF = "- Seu cabelo é prateado.\n+ Seu cabelo é castanho e ela odeia Zeus."
REVERT = "https://w.fandom.com/index.php?title=Hera&action=edit&undoafter=10&undo=11"


def ev(**kw):
    base = {"type": "edit", "wiki": "w", "page": "Hera (Eu)", "user": "Fulano", "comment": "ajuste",
            "timestamp": "t", "accept_link": "https://w.fandom.com/wiki/Hera",
            "revert_link": REVERT, "diff_text": DIFF}
    base.update(kw)
    return base


GOOD = {
    "divergencias": [
        {"alegacao": "cabelo castanho", "conflito": "no seu cânon o cabelo é prateado",
         "pagina": "Hera (Eu)", "trecho": "Seu cabelo é prateado."},
        {"alegacao": "Hera odeia Zeus", "conflito": "inventado",
         "pagina": "Zeus (Eu)", "trecho": "Zeus e Hera são inimigos mortais"},
    ],
    "ambiguos": [{"alegacao": "idade", "motivo": "referência não fecha"}],
    "consistentes": [
        {"alegacao": "Zeus é irmão", "pagina": "Zeus (Eu)"},
        {"alegacao": "algo", "pagina": "Página Inexistente"},
    ],
}


class RaTests(unittest.TestCase):
    def test_retrieval_traz_hera_e_zeus(self):
        chunks = [c for t, x in PAGES.items() for c in ra.chunk_page(t, x)]
        refs = ra.Index(chunks).search("Hera cabelo castanho odeia Zeus", 4)
        titles = {c["title"] for c in refs}
        self.assertIn("Hera (Eu)", titles)
        self.assertIn("Zeus (Eu)", titles)
        self.assertNotIn("[[", " ".join(c["text"] for c in refs))  # marcação limpa

    def test_verify_confirma_e_rebaixa(self):
        refs = [c for t, x in PAGES.items() for c in ra.chunk_page(t, x)]
        out = ra.verify(GOOD, refs)
        self.assertEqual([d["alegacao"] for d in out["divergentes"]], ["cabelo castanho"])
        motivos = [a["motivo"] for a in out["ambiguos"]]
        self.assertTrue(any("não bate" in m for m in motivos))  # citação inventada rebaixada
        self.assertEqual([c["pagina"] for c in out["consistentes"]], ["Zeus (Eu)"])  # página falsa some

    def test_citacao_com_reticencias_e_markup(self):
        refs = [c for t, x in PAGES.items() for c in ra.chunk_page(t, x)]
        r = {"divergencias": [{"alegacao": "a", "conflito": "b", "pagina": "Zeus (Eu)",
                               "trecho": "Zeus é irmão ... governa o Olimpo"}],
             "ambiguos": [], "consistentes": []}
        self.assertEqual(len(ra.verify(r, refs)["divergentes"]), 1)
        r["divergencias"][0]["trecho"] = "curto"  # citação curta demais não vale
        self.assertEqual(len(ra.verify(r, refs)["divergentes"]), 0)

    def _run(self, events, ask):
        env = {"GEMINI_API_KEY": "x"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(ra, "fetch_pages", return_value=dict(PAGES)), \
                mock.patch.object(ra, "fetch_revision", return_value=("Hera (Eu)", OLD_HERA)), \
                mock.patch.object(ra, "ask_gemini_json", side_effect=ask) as m, \
                mock.patch.object(ra.time, "sleep"):
            ra.enrich(events, {"w": {"pages": list(PAGES)}})
            return m

    def test_enrich_pula_vandalismo_mural_e_respeita_teto(self):
        events = [ev(ai="🚨 Vandalismo: x"), {"type": "wall_like", "wiki": "w"}] + [ev() for _ in range(6)]
        m = self._run(events, lambda *a, **k: GOOD)
        self.assertEqual(m.call_count, ra.MAX_EVENTS_PER_RUN)
        self.assertNotIn("canon", events[0])
        self.assertNotIn("canon", events[1])
        self.assertIn("canon", events[2])
        self.assertNotIn("canon", events[-1])  # passou do teto

    def test_enrich_para_no_429_e_nao_levanta(self):
        events = [ev(), ev()]
        self._run(events, RuntimeError("429"))
        self.assertNotIn("canon", events[0])

    def test_enrich_sem_chave(self):
        events = [ev()]
        with mock.patch.dict(os.environ, {}, clear=True):
            ra.enrich(events, {})
        self.assertNotIn("canon", events[0])

    def test_formatos(self):
        e = ev()
        self._run([e], lambda *a, **k: GOOD)
        self.assertLessEqual(len(ra.short(e)), ra.WA_MAX)
        self.assertIn("⚠️", ra.short(e))
        full = ra.long(e)
        for marca in ("⚠️ Divergente:", "❓ Ambíguo", "✅ Consistente:", "fonte: Hera (Eu)"):
            self.assertIn(marca, full)
        vazio = {"canon": {"divergentes": [], "ambiguos": [], "consistentes": []}}
        self.assertIn("sem conflito", ra.short(vazio))
        self.assertIn("Nenhum conflito", ra.long(vazio))

    def test_whatsapp_nunca_perde_os_links(self):
        e = ev(comment="x" * 900)
        self._run([e], lambda *a, **k: GOOD)
        wa = monitor.format_event(e, full=False)[:600]
        self.assertIn(REVERT, wa)
        self.assertIn(e["accept_link"], wa)
        self.assertLessEqual(len(monitor.format_event(e, full=False)), 600)
        self.assertIn("Ra (cânon)", monitor.format_event(e, full=True))  # email completo

    def test_evento_sem_ra_continua_igual(self):
        txt = monitor.format_event(ev(), full=False)
        self.assertNotIn("Ra", txt)
        self.assertIn("Reverter (abre editor)", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
