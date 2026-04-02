import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class GoalTracker:
    """
    Persistent JSON-backed goal tracking system.
    Allows ABLE to maintain long-term context on the user's ultimate objectives.
    """
    def __init__(self, filepath: str = "config/goals.json"):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._load_goals()

    def _load_goals(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    self.goals = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load goals: {e}")
                self.goals = self._default_goals()
        else:
            self.goals = self._default_goals()
            self._save_goals()

    def _default_goals(self) -> Dict[str, Any]:
        return {
            "master_goal": {
                "id": "master_goal",
                "title": "$100k/m by 2/11/2028",
                "target_revenue_mrr": 100000,
                "current_revenue_mrr": 0,
                "deadline": "2028-02-11",
                "kpis": [
                    {"name": "Active Clients", "value": 0},
                    {"name": "Total Deployments", "value": 0},
                    {"name": "Lead Pipeline", "value": 0}
                ],
                "last_updated": datetime.utcnow().isoformat()
            }
        }

    def _save_goals(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self.goals, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save goals: {e}")

    def update_kpi(self, goal_id: str, kpi_name: str, value: float):
        """Update a specific KPI for a goal."""
        if goal_id in self.goals:
            for kpi in self.goals[goal_id].get("kpis", []):
                if kpi["name"].lower() == kpi_name.lower():
                    kpi["value"] = value
                    self.goals[goal_id]["last_updated"] = datetime.utcnow().isoformat()
                    self._save_goals()
                    return True
        return False

    def update_revenue(self, amount: float):
        """Update current MRR for the master goal."""
        if "master_goal" in self.goals:
            self.goals["master_goal"]["current_revenue_mrr"] = amount
            self.goals["master_goal"]["last_updated"] = datetime.utcnow().isoformat()
            self._save_goals()

    def get_summary(self) -> str:
        """Generate a text summary of all current goals for context injection."""
        lines = ["🎯 **Active Goals Status:**"]
        for g_id, g in self.goals.items():
            lines.append(f"\n**{g['title']}** (Deadline: {g['deadline']})")
            lines.append(f"• Progress: ${g['current_revenue_mrr']:,.2f} / ${g['target_revenue_mrr']:,.2f} MRR")
            for kpi in g.get("kpis", []):
                lines.append(f"• {kpi['name']}: {kpi['value']}")
        return "\n".join(lines)
