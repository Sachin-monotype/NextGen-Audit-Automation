"""Desktop Monotype Connect UI automation — trigger ingress events and validate logs."""

from .log_extractor import IngressLogEvent, extract_ingress_events_from_logs
from .navigation import DesktopEvent, load_desktop_events
from .runner import DesktopRunResult, run_desktop_ui_automation

__all__ = [
    "DesktopEvent",
    "DesktopRunResult",
    "IngressLogEvent",
    "extract_ingress_events_from_logs",
    "load_desktop_events",
    "run_desktop_ui_automation",
]
