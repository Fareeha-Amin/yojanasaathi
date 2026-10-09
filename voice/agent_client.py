"""The voice bot's only link to the agent: POST /turn/{case_id}, the same contract web
and phone use. No Pipecat imports (tested from the agent's suite against the real app).
"""

from urllib.parse import quote

import httpx

from voice.lang import TurnLang


class AgentClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def turn(self, case_id: str, text: str, lang: TurnLang | None) -> dict:
        """Send one citizen turn; returns {"reply": str, "pause": dict | None, "ui": dict | None,
        "subtitle": str | None} (subtitle = the reply in English when it is in kn / hi).
        "lang" is omitted when unknown, so the case keeps its previous language."""
        body: dict = {"text": text} if lang is None else {"text": text, "lang": lang}
        r = await self._http.post(f"/turn/{quote(case_id, safe='')}", json=body)
        r.raise_for_status()
        out = r.json()
        return {"reply": out["reply"], "pause": out.get("pause"), "ui": out.get("ui"),
                "subtitle": out.get("subtitle")}

    async def aclose(self) -> None:
        await self._http.aclose()
