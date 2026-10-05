"""Un aviso por turno del cliente; ninguno si ya estas en la charla."""

import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as M

from estiba.bandeja.avisos import merece_aviso

T = datetime(2026, 10, 4, 10, 0)


def _m(i, minutos, cliente=True):
    return M(id=str(i), from_customer=cliente, sent_at=T + timedelta(minutes=minutos))


class TestPolitica:
    def test_el_primer_mensaje_avisa(self):
        a = _m(1, 0)
        assert merece_aviso(a, [a])

    def test_una_rafaga_avisa_una_vez(self):
        ms = [_m(1, 0), _m(2, 1), _m(3, 2), _m(4, 4)]
        assert [merece_aviso(m, ms[: i + 1]) for i, m in enumerate(ms)] == [True, False, False, False]

    def test_si_le_escribiste_recien_no_suena(self):
        ms = [_m(1, 0), _m(2, 3, cliente=False), _m(3, 6)]
        assert not merece_aviso(ms[2], ms)

    def test_un_turno_nuevo_vuelve_a_avisar(self):
        ms = [_m(1, 0), _m(2, 3, cliente=False), _m(3, 30)]
        assert merece_aviso(ms[2], ms)

    def test_despues_de_un_rato_largo_sin_respuesta_avisa_de_nuevo(self):
        ms = [_m(1, 0), _m(2, 45)]
        assert merece_aviso(ms[1], ms)

    def test_lo_tuyo_nunca_avisa(self):
        a = _m(1, 0, cliente=False)
        assert not merece_aviso(a, [a])


class TestUnaSincronizacionPorVez:
    def test_dos_hilos_no_se_pisan(self, tmp_path, monkeypatch):
        import estiba.web.app as A
        from estiba.storage.db import connect

        ruta = tmp_path / "x.db"
        connect(ruta).close()
        adentro, maximo = [0], [0]

        def lenta(*_a, **_k):
            adentro[0] += 1
            maximo[0] = max(maximo[0], adentro[0])
            time.sleep(0.05)
            adentro[0] -= 1
            return A.Resultado()

        monkeypatch.setattr(A, "sincronizar", lenta)
        e = A.Estado(str(ruta), A.JobEngine(), datetime.now)
        hilos = [threading.Thread(target=e.sincronizar) for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert maximo[0] == 1
