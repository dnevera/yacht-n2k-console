"""
AutopilotState — current state of the Raymarine Evolution autopilot (READ ONLY).

Fed from the frames decoded by n2k_autopilot.decode_frame(). One instance per
boat: there is a single course computer, so `src` is kept for observability
rather than as a key.

Fields for which no frame has arrived (or which arrived as "data not
available") stay None — they are never faked into zeros.
"""
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AutopilotState:
    """Autopilot channel — Raymarine proprietary PGNs + standard 127237/127245."""
    mode: str = "unknown"
    locked_heading_deg: Optional[float] = None
    heading_reference: Optional[str] = None
    wind_datum_deg: Optional[float] = None
    rudder_angle_deg: Optional[float] = None
    src: Optional[int] = None
    last_update: float = 0.0

    @property
    def age_sec(self) -> Optional[float]:
        if self.last_update > 0:
            return round(time.time() - self.last_update, 1)
        return None

    def update_from_frame(self, decoded: Dict[str, Any], src: Optional[int] = None) -> None:
        """Apply one decoded autopilot frame. Called from the bus worker loop."""
        if not decoded:
            return
        kind = decoded.get("kind")
        if kind == "pilot_mode":
            self.mode = decoded.get("mode", "unknown")
        elif kind == "locked_heading":
            self.locked_heading_deg = decoded.get("locked_heading_deg")
            self.heading_reference = decoded.get("heading_reference")
        elif kind == "wind_datum":
            self.wind_datum_deg = decoded.get("wind_datum_deg")
        elif kind == "rudder":
            self.rudder_angle_deg = decoded.get("rudder_angle_deg")
        elif kind == "heading_track_control":
            # Only used as a fallback: the proprietary locked heading is the
            # value the p70 actually displays.
            if self.locked_heading_deg is None:
                self.locked_heading_deg = decoded.get("heading_to_steer_deg")
        else:
            return
        if src is not None:
            self.src = src
        self.last_update = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "locked_heading_deg": self.locked_heading_deg,
            "heading_reference": self.heading_reference,
            "wind_datum_deg": self.wind_datum_deg,
            "rudder_angle_deg": self.rudder_angle_deg,
            "src": self.src,
            "last_update": self.last_update or None,
            "age_sec": self.age_sec,
        }
