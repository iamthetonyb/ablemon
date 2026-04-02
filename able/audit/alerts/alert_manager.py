"""
Alert Manager
Security and operational alerting with Telegram notifications.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertCategory(Enum):
    SECURITY = "security"
    OPERATIONAL = "operational"
    BILLING = "billing"
    PERFORMANCE = "performance"
    CLIENT = "client"


@dataclass
class Alert:
    """An alert instance"""
    id: str
    severity: AlertSeverity
    category: AlertCategory
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


class AlertManager:
    """
    Manages alerts, notifications, and escalations.
    Integrates with Telegram for real-time notifications.
    """

    # Rate limiting
    RATE_LIMIT_WINDOW = timedelta(minutes=5)
    MAX_ALERTS_PER_WINDOW = 10

    def __init__(
        self,
        alerts_dir: Optional[Path] = None,
        telegram_bot_token: Optional[str] = None,
        owner_telegram_id: Optional[str] = None
    ):
        self.alerts_dir = alerts_dir or Path(__file__).parent
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

        self.telegram_token = telegram_bot_token
        self.owner_id = owner_telegram_id

        # Load from v1 secrets if not provided
        if not self.telegram_token:
            secret_path = Path.home() / ".able" / ".secrets" / "TELEGRAM_BOT_TOKEN"
            if secret_path.exists():
                self.telegram_token = secret_path.read_text().strip()

        # In-memory alert storage
        self.alerts: Dict[str, Alert] = {}
        self.rate_limit_counts: Dict[str, List[datetime]] = {}

        # Callbacks
        self.alert_handlers: List[Callable[[Alert], None]] = []

        # Load existing alerts
        self._load_alerts()

    def _load_alerts(self):
        """Load alerts from disk"""
        alerts_file = self.alerts_dir / "active_alerts.json"
        if alerts_file.exists():
            with open(alerts_file) as f:
                data = json.load(f)
                for alert_data in data.get("alerts", []):
                    alert = Alert(
                        id=alert_data["id"],
                        severity=AlertSeverity(alert_data["severity"]),
                        category=AlertCategory(alert_data["category"]),
                        title=alert_data["title"],
                        message=alert_data["message"],
                        timestamp=datetime.fromisoformat(alert_data["timestamp"]),
                        source=alert_data.get("source", "system"),
                        metadata=alert_data.get("metadata", {}),
                        acknowledged=alert_data.get("acknowledged", False)
                    )
                    self.alerts[alert.id] = alert

    def _save_alerts(self):
        """Save alerts to disk"""
        alerts_file = self.alerts_dir / "active_alerts.json"
        data = {
            "alerts": [
                {
                    "id": a.id,
                    "severity": a.severity.value,
                    "category": a.category.value,
                    "title": a.title,
                    "message": a.message,
                    "timestamp": a.timestamp.isoformat(),
                    "source": a.source,
                    "metadata": a.metadata,
                    "acknowledged": a.acknowledged
                }
                for a in self.alerts.values()
            ]
        }
        with open(alerts_file, "w") as f:
            json.dump(data, f, indent=2)

    def _generate_id(self) -> str:
        """Generate unique alert ID"""
        import hashlib
        return hashlib.sha256(
            f"{datetime.utcnow().isoformat()}:{len(self.alerts)}".encode()
        ).hexdigest()[:12]

    def _is_rate_limited(self, category: AlertCategory) -> bool:
        """Check if alerts are rate limited"""
        key = category.value
        now = datetime.utcnow()

        # Clean old entries
        if key in self.rate_limit_counts:
            self.rate_limit_counts[key] = [
                t for t in self.rate_limit_counts[key]
                if now - t < self.RATE_LIMIT_WINDOW
            ]
        else:
            self.rate_limit_counts[key] = []

        return len(self.rate_limit_counts[key]) >= self.MAX_ALERTS_PER_WINDOW

    def create_alert(
        self,
        severity: AlertSeverity,
        category: AlertCategory,
        title: str,
        message: str,
        source: str = "system",
        metadata: Dict[str, Any] = None,
        notify: bool = True
    ) -> Alert:
        """Create a new alert"""
        # Check rate limiting
        if self._is_rate_limited(category):
            # Still log but don't notify
            notify = False

        alert = Alert(
            id=self._generate_id(),
            severity=severity,
            category=category,
            title=title,
            message=message,
            source=source,
            metadata=metadata or {}
        )

        self.alerts[alert.id] = alert

        # Update rate limit
        if category.value not in self.rate_limit_counts:
            self.rate_limit_counts[category.value] = []
        self.rate_limit_counts[category.value].append(datetime.utcnow())

        # Log to file
        self._log_alert(alert)

        # Save
        self._save_alerts()

        # Notify
        if notify and severity in [AlertSeverity.ERROR, AlertSeverity.CRITICAL]:
            asyncio.create_task(self._send_telegram_notification(alert))

        # Call handlers
        for handler in self.alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                print(f"Alert handler error: {e}")

        return alert

    def _log_alert(self, alert: Alert):
        """Log alert to JSONL file"""
        log_file = self.alerts_dir / "alerts.jsonl"
        entry = {
            "id": alert.id,
            "timestamp": alert.timestamp.isoformat(),
            "severity": alert.severity.value,
            "category": alert.category.value,
            "title": alert.title,
            "message": alert.message,
            "source": alert.source,
            "metadata": alert.metadata
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    async def _send_telegram_notification(self, alert: Alert):
        """Send alert via Telegram"""
        if not self.telegram_token or not self.owner_id:
            return

        try:
            import aiohttp

            severity_emoji = {
                AlertSeverity.INFO: "ℹ️",
                AlertSeverity.WARNING: "⚠️",
                AlertSeverity.ERROR: "❌",
                AlertSeverity.CRITICAL: "🚨"
            }

            emoji = severity_emoji.get(alert.severity, "📢")
            text = f"{emoji} **{alert.severity.value.upper()}**: {alert.title}\n\n{alert.message}"

            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                await session.post(url, json={
                    "chat_id": self.owner_id,
                    "text": text,
                    "parse_mode": "Markdown"
                })

        except Exception as e:
            print(f"Telegram notification error: {e}")

    def acknowledge(self, alert_id: str, acknowledged_by: str = "system") -> bool:
        """Acknowledge an alert"""
        if alert_id not in self.alerts:
            return False

        alert = self.alerts[alert_id]
        alert.acknowledged = True
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.utcnow()

        self._save_alerts()
        return True

    def get_active_alerts(
        self,
        severity: Optional[AlertSeverity] = None,
        category: Optional[AlertCategory] = None,
        include_acknowledged: bool = False
    ) -> List[Alert]:
        """Get active alerts with optional filters"""
        alerts = list(self.alerts.values())

        if not include_acknowledged:
            alerts = [a for a in alerts if not a.acknowledged]

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        if category:
            alerts = [a for a in alerts if a.category == category]

        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)

    def security_alert(self, title: str, message: str, metadata: Dict = None):
        """Convenience method for security alerts"""
        return self.create_alert(
            severity=AlertSeverity.CRITICAL,
            category=AlertCategory.SECURITY,
            title=title,
            message=message,
            metadata=metadata
        )

    def operational_warning(self, title: str, message: str, metadata: Dict = None):
        """Convenience method for operational warnings"""
        return self.create_alert(
            severity=AlertSeverity.WARNING,
            category=AlertCategory.OPERATIONAL,
            title=title,
            message=message,
            metadata=metadata
        )

    def add_handler(self, handler: Callable[[Alert], None]):
        """Add an alert handler callback"""
        self.alert_handlers.append(handler)
