"""PrismRAG alerting — admin notifications and client-facing error emails."""
from prismrag.alerting.alerts import alert_admin, alert_client, ErrorSeverity

__all__ = ["alert_admin", "alert_client", "ErrorSeverity"]
