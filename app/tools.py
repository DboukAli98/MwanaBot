from typing import Any

import httpx
from langchain_core.tools import tool

from app.config import Settings


class SchoolFeesClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.schoolfees_api_base_url.rstrip("/")
        self.token = settings.schoolfees_api_token

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def get_student_balance(self, student_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=20) as client:
            response = await client.get(f"/api/students/{student_id}/balance")
            response.raise_for_status()
            return response.json()


def build_schoolfees_tools(settings: Settings):
    client = SchoolFeesClient(settings)

    @tool
    async def consulter_solde_eleve(student_id: str) -> str:
        """Consulte le solde scolaire d'un élève depuis l'API SchoolFees."""
        data = await client.get_student_balance(student_id)
        return str(data)

    return [consulter_solde_eleve]

