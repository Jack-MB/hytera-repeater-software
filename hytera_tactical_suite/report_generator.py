"""
Hytera Tactical Suite - Einsatzbericht & Lageprotokoll Generator
Aggregiert Vorfälle, Funkverkehr, eingesetzte Kräfte und Telemetriedaten
zu einem standardisierten, druckfähigen taktischen Einsatzbericht (BOS-Standard).
"""

from typing import Dict, Any, List
try:
    from .db_manager import DatabaseManager, get_current_iso_timestamp
    from .hardware_monitor import hw_monitor
except (ImportError, ValueError):
    from db_manager import DatabaseManager, get_current_iso_timestamp
    from hardware_monitor import hw_monitor


class ReportGenerator:
    """Erzeugt strukturierte Einsatzberichte für Nachbereitung und Behördendokumentation."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    async def generate_mission_report(self) -> Dict[str, Any]:
        """Kompiliert alle Daten des laufenden Einsatzes."""
        now_ts = get_current_iso_timestamp()

        # 1. Funkgeräte / Einsatzkräfte
        radios = await self.db.get_radios()

        # 2. Alle Vorfälle & Marker
        markers = await self.db.get_markers()

        # 3. Funkgespräche (letzte 50 Transaktionen)
        calls = await self.db.get_recent_calls(limit=100)

        # 4. Aktive Pläne & Geofences
        plans = await self.db.get_plans()
        geofences = await self.db.get_geofences()

        # 5. Hardware- & Notstromzustand
        infra = hw_monitor.get_all_telemetry()

        # Einsatzdauer & Metadaten
        first_call = calls[-1]["timestamp"] if calls else now_ts
        mission = await self.db.get_mission_data()

        return {
            "title": mission.get("mission_title") or "TAKTISCHER LAGEBERICHT & EINSATZPROTOKOLL",
            "mission_id": mission.get("mission_code") or f"EINSATZ-{now_ts[:10].replace('-', '')}-01",
            "generated_at": now_ts,
            "start_time": mission.get("mission_start_time") or first_call,
            "author": mission.get("mission_leader") or "Leitstelle / ELW 1",
            "location": mission.get("mission_location") or "Einsatzgebiet",
            "channel": mission.get("mission_channel") or "Hytera HR1065 (Kanal 1 / TS1 & TS2)",
            "status": mission.get("mission_status") or "running",
            "notes": mission.get("mission_notes") or "",
            "summary": {
                "total_radios": len(radios),
                "gps_radios": len([r for r in radios if r.get("has_gps")]),
                "no_gps_radios": len([r for r in radios if not r.get("has_gps")]),
                "total_incidents": len(markers),
                "critical_incidents": len([m for m in markers if m.get("prioritaet") == "critical"]),
                "total_calls": len(calls),
                "active_emergencies": len([r for r in radios if r.get("is_emergency")])
            },
            "radios": radios,
            "markers": markers,
            "calls": calls,
            "plans": plans,
            "geofences": geofences,
            "infrastructure": infra
        }
