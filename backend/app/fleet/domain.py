"""fleet domain — pure enums + value objects. No framework imports.

``EQUIPMENT`` is the same vocabulary as ``FreightRun.equipment`` on purpose, so
a truck and the runs it hauled can be joined and filtered with one word.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

EQUIPMENT: tuple[str, ...] = ("van", "reefer", "flatbed", "stepdeck")


class UnitKind(StrEnum):
    TRUCK = "truck"
    TRAILER = "trailer"


class TruckStatus(StrEnum):
    AVAILABLE = "available"
    ON_LOAD = "on_load"
    IN_SHOP = "in_shop"
    OUT_OF_SERVICE = "out_of_service"


class InspectionResult(StrEnum):
    PASS = "pass"
    CONDITIONAL = "conditional"
    FAIL = "fail"


class DefectSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class DefectStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class MaintenanceKind(StrEnum):
    SERVICE = "service"
    REPAIR = "repair"
    TYRE = "tyre"
    OTHER = "other"


class DocumentKind(StrEnum):
    REGISTRATION = "registration"
    INSURANCE = "insurance"
    INSPECTION_CERT = "inspection_cert"
    ADR = "adr"
    TACHO_CALIBRATION = "tacho_calibration"


@dataclass(frozen=True, slots=True)
class Odometer:
    """Miles on the clock. Never negative, and it never goes down.

    The "odometer only moves forward" rule lives here once, so a bad import
    from a future vendor feed fails in one place instead of corrupting history.
    """

    miles: int

    def __post_init__(self) -> None:
        if self.miles < 0:
            raise ValueError("odometer cannot be negative")

    def advance_to(self, miles: int) -> Odometer:
        if miles < self.miles:
            raise ValueError(f"odometer cannot go backwards ({self.miles} -> {miles})")
        return Odometer(miles)
