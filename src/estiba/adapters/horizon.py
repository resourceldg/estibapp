"""Horizon: el tablero general del fletero, en la nube.

Estiba es duena de los fletes (precio, agenda, cobro). Horizon los muestra
resumidos junto con los otros oficios del usuario y los suma a sus
objetivos, pero no los recalcula ni los edita.

La conexion la inicia siempre Estiba: Horizon esta en Vercel y no puede
entrar a esta compu, y abrirla a internet seria exponer los datos de los
clientes. Si la compu esta apagada, Horizon muestra lo ultimo que recibio.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

#: Cuantos fletes por envio. Horizon acepta hasta 60; cada uno son varias
#: consultas a su base, asi que de a poco no se pasa del tiempo de Vercel.
TANDA = 40


class ErrorHorizon(Exception):
    pass


@dataclass(frozen=True)
class ConfigHorizon:
    url: str
    token: str

    @classmethod
    def desde_entorno(cls) -> ConfigHorizon | None:
        url, token = os.environ.get("ESTIBA_HORIZON_URL"), os.environ.get("ESTIBA_HORIZON_TOKEN")
        if not url or not token:
            return None
        return cls(url=url.rstrip("/"), token=token)


class HorizonClient:
    def __init__(self, config: ConfigHorizon, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._http = httpx.Client(base_url=config.url, transport=transport, timeout=45,
                                  headers={"Authorization": f"Bearer {config.token}"})

    def _post(self, cuerpo: dict) -> dict:
        try:
            r = self._http.post("/api/estiba", json=cuerpo)
        except httpx.HTTPError as e:
            raise ErrorHorizon(f"no se pudo hablar con Horizon en {self.config.url}: {e}") from e
        if r.status_code == 401:
            raise ErrorHorizon("Horizon rechazo el token: revisa ESTIBA_HORIZON_TOKEN y HORIZON_ESTIBA_TOKEN en Vercel")
        if r.status_code >= 400:
            try:
                detalle = r.json().get("error")
            except ValueError:
                detalle = None
            raise ErrorHorizon(f"Horizon respondio {r.status_code}: {detalle or r.text[:200]}")
        return r.json()

    def comandos(self) -> list[dict]:
        """Los cambios de estado que el usuario pidio desde Horizon y esperan a Estiba."""
        try:
            r = self._http.get("/api/estiba", params={"comandos": "1"})
        except httpx.HTTPError as e:
            raise ErrorHorizon(f"no se pudo hablar con Horizon en {self.config.url}: {e}") from e
        if r.status_code >= 400:
            raise ErrorHorizon(f"Horizon respondio {r.status_code} al pedir los cambios pendientes")
        return list(r.json().get("comandos") or [])

    def enviar(self, fletes: list[dict], mes: dict | None, enviado: str,
               acks: list[dict] | None = None, catalogos: dict | None = None) -> dict:
        """Manda los fletes de a tandas y el resumen del mes con la ultima.

        Devuelve el total de creados, actualizados y precios nuevos, y los
        ids que Horizon no pudo guardar (para no darlos por enviados).
        """
        total = {"creados": 0, "actualizados": 0, "precios": 0, "fallidos": []}
        tandas = [fletes[i:i + TANDA] for i in range(0, len(fletes), TANDA)] or [[]]
        for n, tanda in enumerate(tandas):
            cuerpo = {"version": 1, "enviado": enviado, "fletes": tanda}
            # Las respuestas a los cambios van en la primera tanda: Horizon las procesa antes que los fletes.
            if n == 0 and acks:
                cuerpo["acks"] = acks
            if n == len(tandas) - 1 and mes is not None:
                cuerpo["mes"] = mes
            if n == len(tandas) - 1 and catalogos is not None:
                cuerpo["catalogos"] = catalogos
            r = self._post(cuerpo)
            for clave in ("creados", "actualizados", "precios"):
                total[clave] += int(r.get(clave) or 0)
            total["fallidos"] += [e.get("id") for e in r.get("errores") or [] if e.get("id")]
        return total
