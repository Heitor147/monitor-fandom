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


def gem(veredito="legítima", **kw):
    base = {"resumo": "Trocou a cor do cabelo.", "veredito": veredito,
            "divergencias": [], "ambiguos": [], "consistentes": []}
    base.update(kw)
    return base


GOOD = gem(
    "legítima",
    divergencias=[
        {"alegacao": "cabelo castanho", "conflito": "no seu cânon o cabelo é prateado",
         "pagina": "Hera (Eu)", "trecho": "Seu cabelo é prateado."},
        {"alegacao": "Hera odeia Zeus", "conflito": "inventado",
         "pagina": "Zeus (Eu)", "trecho": "Zeus e Hera são inimigos mortais"},
    ],
    ambiguos=[{"alegacao": "idade", "motivo": "referência não fecha"}],
    consistentes=[
        {"alegacao": "Zeus é irmão", "pagina": "Zeus (Eu)"},
        {"alegacao": "algo", "pagina": "Página Inexistente"},
    ],
)


def refs_all():
    return [c for t, x in PAGES.items() for c in ra.chunk_page(t, x)]


class RaTests(unittest.TestCase):
    def test_retrieval_traz_hera_e_zeus(self):
        refs = ra.Index(refs_all()).search("Hera cabelo castanho odeia Zeus", 4)
        titles = {c["title"] for c in refs}
        self.assertIn("Hera (Eu)", titles)
        self.assertIn("Zeus (Eu)", titles)
        self.assertNotIn("[[", " ".join(c["text"] for c in refs))

    def test_verify_confirma_e_rebaixa(self):
        out = ra.verify(GOOD, refs_all())
        self.assertEqual([d["alegacao"] for d in out["divergentes"]], ["cabelo castanho"])
        self.assertTrue(any("não bate" in a["motivo"] for a in out["ambiguos"]))
        self.assertEqual([c["pagina"] for c in out["consistentes"]], ["Zeus (Eu)"])

    def test_citacao_com_reticencias_e_markup(self):
        r = gem(divergencias=[{"alegacao": "a", "conflito": "b", "pagina": "Zeus (Eu)",
                               "trecho": "Zeus é irmão ... governa o Olimpo"}])
        self.assertEqual(len(ra.verify(r, refs_all())["divergentes"]), 1)
        r["divergencias"][0]["trecho"] = "curto"
        self.assertEqual(len(ra.verify(r, refs_all())["divergentes"]), 0)

    # ---- veredito único
    def test_divergencia_confirmada_impede_legitima(self):
        line, canon = ra.judge(GOOD, refs_all())  # o modelo disse "legítima", mas há divergência
        self.assertTrue(line.startswith("⚠️ Suspeita"))
        self.assertEqual(len(canon["divergentes"]), 1)

    def test_citacao_falsa_nao_derruba_legitima(self):
        r = gem("legítima", divergencias=[{"alegacao": "x", "conflito": "y",
                                           "pagina": "Zeus (Eu)", "trecho": "frase que não existe no texto"}])
        line, canon = ra.judge(r, refs_all())
        self.assertTrue(line.startswith("✅ Legítima"))
        self.assertEqual(canon["divergentes"], [])

    def test_vandalismo_e_sem_referencia_nao_geram_relatorio(self):
        line, canon = ra.judge(gem("vandalismo"), refs_all())
        self.assertTrue(line.startswith("🚨 Vandalismo"))
        self.assertIsNone(canon)
        line, canon = ra.judge(gem("legítima"), [])
        self.assertTrue(line.startswith("✅ Legítima"))
        self.assertIsNone(canon)

    def test_veredito_invalido_vira_suspeita(self):
        line, _ = ra.judge(gem("talvez"), refs_all())
        self.assertTrue(line.startswith("⚠️ Suspeita"))

    def test_inconclusiva_explicita_motivo_e_pede_investigacao(self):
        line, canon = ra.judge(
            gem("inconclusiva", resumo="O diff veio truncado e não dá para entender a alteração."),
            refs_all(),
        )
        self.assertTrue(line.startswith("❔ Inconclusiva"))
        self.assertIn("diff veio truncado", line)
        self.assertIn("Confira a edição manualmente", line)
        self.assertIsNone(canon)

    def test_inconclusiva_sem_motivo_tem_fallback_e_nao_tem_canon(self):
        line, canon = ra.judge(gem("inconclusiva", resumo=""), refs_all())
        self.assertIn("não foi possível interpretar", line)
        self.assertIn("Confira a edição manualmente", line)
        self.assertIsNone(canon)

    # ---- enrich
    def _run(self, events, ask):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), \
                mock.patch.object(ra, "fetch_pages", return_value=dict(PAGES)), \
                mock.patch.object(ra, "fetch_revision", return_value=("Hera (Eu)", OLD_HERA)), \
                mock.patch.object(ra, "ask_gemini_json", side_effect=ask) as m, \
                mock.patch.object(ra.time, "sleep"):
            ra.enrich(events, {"w": {"pages": list(PAGES)}})
            return m

    def test_enrich_pula_revert_e_mural_e_respeita_teto(self):
        events = [ev(comment="Undo revision 123 by X"), ev(comment="Revert vandalism"),
                  {"type": "wall_like", "wiki": "w"}] + [ev() for _ in range(ra.MAX_EVENTS_PER_RUN + 2)]
        m = self._run(events, lambda *a, **k: GOOD)
        self.assertEqual(m.call_count, ra.MAX_EVENTS_PER_RUN)
        for i in range(3):
            self.assertNotIn("ai", events[i])
        self.assertIn("ai", events[3])
        self.assertNotIn("ai", events[-1])

    def test_enrich_vandalismo_tem_veredito_sem_canon(self):
        e = ev()
        self._run([e], lambda *a, **k: gem("vandalismo", resumo="Piada solta."))
        self.assertTrue(e["ai"].startswith("🚨"))
        self.assertNotIn("canon", e)

    def test_enrich_para_no_429_e_nao_levanta(self):
        events = [ev(), ev()]
        self._run(events, RuntimeError("429"))
        self.assertNotIn("ai", events[0])

    def test_falha_no_canon_ainda_da_veredito(self):
        e = ev()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), \
                mock.patch.object(ra, "fetch_pages", side_effect=OSError("wiki fora")), \
                mock.patch.object(ra, "ask_gemini_json", return_value=gem("legítima")), \
                mock.patch.object(ra.time, "sleep"):
            ra.enrich([e], {"w": {"pages": list(PAGES)}})
        self.assertTrue(e["ai"].startswith("✅"))
        self.assertNotIn("canon", e)

    def test_enrich_sem_chave(self):
        events = [ev()]
        with mock.patch.dict(os.environ, {}, clear=True):
            ra.enrich(events, {})
        self.assertNotIn("ai", events[0])

    def test_plano_b_so_preenche_quem_o_ra_nao_julgou(self):
        julgado, revert = ev(ai="✅ Legítima: x"), ev(comment="Undo revision 1")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), \
                mock.patch.object(ai_summary, "fetch_diff", return_value="+ a"), \
                mock.patch.object(ai_summary, "ask_gemini",
                                  return_value={"resumo": "Desfez edição.", "veredito": "legítima"}) as m, \
                mock.patch.object(ai_summary.time, "sleep"):
            ai_summary.enrich([julgado, revert])
        self.assertEqual(m.call_count, 1)
        self.assertEqual(julgado["ai"], "✅ Legítima: x")
        self.assertTrue(revert["ai"].startswith("✅ Legítima"))

    # ---- formatos
    def test_formatos(self):
        e = ev()
        self._run([e], lambda *a, **k: GOOD)
        self.assertLessEqual(len(ra.short(e)), ra.WA_MAX)
        self.assertIn("Hera (Eu)", ra.short(e))
        full = ra.long(e)
        for marca in ("⚠️ Divergente:", "❓ Ambíguo", "✅ Consistente:", "fonte: Hera (Eu)"):
            self.assertIn(marca, full)
        vazio = {"canon": {"divergentes": [], "ambiguos": [], "consistentes": []}}
        self.assertEqual(ra.short(vazio), "")  # sem conflito não gasta espaço no WhatsApp
        self.assertIn("sem conflito", ra.long(vazio))

    def test_aviso_tem_um_unico_veredito_do_ra(self):
        e = ev()
        self._run([e], lambda *a, **k: GOOD)
        for full in (True, False):
            txt = monitor.format_event(e, full=full)
            self.assertEqual(txt.count("🔆 Ra:"), 1)
            self.assertNotIn("IA:", txt)

    def test_whatsapp_nunca_perde_os_links(self):
        e = ev(comment="x" * 900)
        self._run([e], lambda *a, **k: GOOD)
        wa = monitor.format_event(e, full=False)
        self.assertIn(REVERT, wa)
        self.assertIn(e["accept_link"], wa)
        self.assertLessEqual(len(wa), 600)
        self.assertIn("Cânon:", monitor.format_event(e, full=True))

    def test_evento_sem_ra_continua_igual(self):
        txt = monitor.format_event(ev(), full=False)
        self.assertNotIn("Ra", txt)
        self.assertIn("Reverter (abre editor)", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
