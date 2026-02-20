"""
ATLAS Self-Improvement System - Autonomous document updates with approval.

Allows ATLAS to update its own configuration and learning documents
through a rigorous approval process, similar to skill creation.

This is what makes ATLAS truly AGI-like:
- Learns from experiences and updates its knowledge base
- Proposes improvements to its own prompts and procedures
- Requires human approval for sensitive document changes
- Maintains audit trail of all self-modifications

Protected documents (require approval):
- CLAUDE.md (core operating instructions)
- identity.yaml (operator configuration)
- current_objectives.yaml (task tracking)

Auto-updateable documents:
- learnings.md (can append without approval)
- daily logs (always auto-updated)
- SKILL_INDEX.yaml (through skill approval process)
"""

import asyncio
import difflib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable

logger = logging.getLogger(__name__)


class DocumentType(Enum):
    """Classification of document sensitivity"""
    CORE = "core"           # CLAUDE.md, identity.yaml - always needs approval
    OBJECTIVES = "objectives"  # current_objectives.yaml - approval for structure changes
    LEARNING = "learning"   # learnings.md, insights - can auto-append
    LOG = "log"             # daily logs, audit - always auto-updated
    SKILL = "skill"         # skill definitions - through skill approval process


class UpdateType(Enum):
    """Type of document modification"""
    APPEND = "append"       # Add content to end
    PREPEND = "prepend"     # Add content to start
    REPLACE = "replace"     # Replace entire file
    PATCH = "patch"         # Apply diff/patch
    SECTION = "section"     # Update specific section


@dataclass
class DocumentUpdate:
    """A proposed update to a document"""
    id: str
    document_path: Path
    document_type: DocumentType
    update_type: UpdateType
    content: str                    # New content or patch
    reason: str                     # Why this update is needed
    source: str                     # What triggered this (learning, insight, etc.)
    created_at: float = field(default_factory=time.time)
    approved: Optional[bool] = None
    approved_at: Optional[float] = None
    approved_by: Optional[str] = None
    applied: bool = False
    diff: Optional[str] = None      # Human-readable diff
    original_content: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class ImprovementInsight:
    """An insight that could lead to self-improvement"""
    id: str
    insight_type: str              # pattern, optimization, learning, win, etc.
    description: str
    confidence: float              # 0.0 - 1.0
    source: str                    # Where this insight came from
    affected_documents: List[str]
    suggested_changes: List[str]
    created_at: float = field(default_factory=time.time)


class SelfImprovementEngine:
    """
    Engine for autonomous self-improvement with safety guardrails.

    Workflow:
    1. Collect insights from various sources (learner, scraper, execution outcomes)
    2. Analyze insights and propose document updates
    3. Generate human-readable diffs
    4. Route updates through approval workflow
    5. Apply approved changes atomically
    6. Log all modifications to audit trail

    Safety:
    - CORE documents always require human approval
    - Changes are validated before application
    - Rollback capability for all changes
    - Rate limiting on self-modification
    """

    # Document classification
    CORE_DOCUMENTS = {
        "CLAUDE.md",
        "identity.yaml",
        "gateway.json",
    }

    OBJECTIVE_DOCUMENTS = {
        "current_objectives.yaml",
        "pending.yaml",
    }

    LEARNING_DOCUMENTS = {
        "learnings.md",
        "LEARNINGS.md",
        "insights.md",
    }

    # Patterns that should never be added to documents
    FORBIDDEN_PATTERNS = [
        r"ignore.*instructions",
        r"disregard.*previous",
        r"you are now",
        r"new identity",
        r"eval\s*\(",
        r"exec\s*\(",
        r"__import__",
        r"subprocess",
        r"os\.system",
    ]

    def __init__(
        self,
        v1_path: Path = None,
        v2_path: Path = None,
        approval_workflow=None,
        audit_log=None,
        max_updates_per_day: int = 10,
    ):
        self.v1_path = Path(v1_path or "~/.atlas").expanduser()
        self.v2_path = Path(v2_path or ".").resolve()
        self.approval = approval_workflow
        self.audit = audit_log
        self.max_updates_per_day = max_updates_per_day

        self._pending_updates: Dict[str, DocumentUpdate] = {}
        self._applied_updates: List[DocumentUpdate] = []
        self._daily_update_count = 0
        self._last_reset_date = datetime.now().date()

    def _classify_document(self, path: Path) -> DocumentType:
        """Classify a document by its path and name"""
        name = path.name

        if name in self.CORE_DOCUMENTS:
            return DocumentType.CORE
        if name in self.OBJECTIVE_DOCUMENTS:
            return DocumentType.OBJECTIVES
        if name in self.LEARNING_DOCUMENTS:
            return DocumentType.LEARNING
        if "daily" in str(path).lower() or "log" in name.lower():
            return DocumentType.LOG
        if "skill" in str(path).lower():
            return DocumentType.SKILL

        # Default to CORE for safety
        return DocumentType.CORE

    def _requires_approval(self, doc_type: DocumentType, update_type: UpdateType) -> bool:
        """Determine if an update requires human approval"""
        # CORE always needs approval
        if doc_type == DocumentType.CORE:
            return True

        # OBJECTIVES need approval for structural changes
        if doc_type == DocumentType.OBJECTIVES and update_type in (UpdateType.REPLACE, UpdateType.PATCH):
            return True

        # LEARNING can auto-append but not replace
        if doc_type == DocumentType.LEARNING and update_type == UpdateType.REPLACE:
            return True

        # SKILL always needs approval (handled by skill system)
        if doc_type == DocumentType.SKILL:
            return True

        return False

    def _validate_content(self, content: str) -> Tuple[bool, str]:
        """Validate content doesn't contain forbidden patterns"""
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Forbidden pattern detected: {pattern}"
        return True, "Content validated"

    def _generate_diff(self, original: str, new: str) -> str:
        """Generate human-readable diff"""
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile='original',
            tofile='updated',
            lineterm=''
        )
        return ''.join(diff)

    def _generate_update_id(self) -> str:
        """Generate unique update ID"""
        return f"upd_{int(time.time())}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"

    async def propose_update(
        self,
        document_path: Path,
        content: str,
        update_type: UpdateType,
        reason: str,
        source: str = "self_improvement",
    ) -> DocumentUpdate:
        """
        Propose an update to a document.

        Args:
            document_path: Path to the document
            content: New content or content to append
            update_type: Type of update
            reason: Why this update is needed
            source: What triggered this update

        Returns:
            DocumentUpdate object (may be pending approval)
        """
        # Check daily rate limit
        today = datetime.now().date()
        if today != self._last_reset_date:
            self._daily_update_count = 0
            self._last_reset_date = today

        if self._daily_update_count >= self.max_updates_per_day:
            raise RuntimeError(f"Daily update limit ({self.max_updates_per_day}) reached")

        # Resolve path
        path = Path(document_path)
        if not path.is_absolute():
            # Try v1 path first, then v2
            if (self.v1_path / path).exists():
                path = self.v1_path / path
            else:
                path = self.v2_path / path

        # Validate content
        valid, msg = self._validate_content(content)
        if not valid:
            raise ValueError(f"Content validation failed: {msg}")

        # Classify document
        doc_type = self._classify_document(path)

        # Read original content
        original_content = ""
        if path.exists():
            original_content = path.read_text()

        # Generate new content based on update type
        if update_type == UpdateType.APPEND:
            new_content = original_content + "\n" + content
        elif update_type == UpdateType.PREPEND:
            new_content = content + "\n" + original_content
        elif update_type == UpdateType.REPLACE:
            new_content = content
        else:
            new_content = content  # PATCH/SECTION handled separately

        # Generate diff
        diff = self._generate_diff(original_content, new_content)

        # Create update object
        update = DocumentUpdate(
            id=self._generate_update_id(),
            document_path=path,
            document_type=doc_type,
            update_type=update_type,
            content=content,
            reason=reason,
            source=source,
            diff=diff,
            original_content=original_content,
        )

        # Route through approval if needed
        needs_approval = self._requires_approval(doc_type, update_type)

        if needs_approval and self.approval:
            # Store as pending and request approval
            self._pending_updates[update.id] = update

            approval_result = await self.approval.request_approval(
                operation=f"Update {path.name}",
                details={
                    "document": str(path),
                    "type": doc_type.value,
                    "update_type": update_type.value,
                    "reason": reason,
                    "diff_preview": diff[:1000] if diff else "No diff available",
                },
                timeout_seconds=3600,  # 1 hour timeout
            )

            update.approved = approval_result.approved
            update.approved_at = time.time()
            update.approved_by = getattr(approval_result, 'approved_by', 'operator')

            if approval_result.approved:
                await self._apply_update(update)

        elif not needs_approval:
            # Auto-apply for safe updates
            update.approved = True
            update.approved_at = time.time()
            update.approved_by = "auto"
            await self._apply_update(update)

        else:
            # No approval workflow, store as pending
            self._pending_updates[update.id] = update
            logger.warning(f"Update {update.id} pending manual approval (no workflow configured)")

        return update

    async def _apply_update(self, update: DocumentUpdate):
        """Apply an approved update"""
        try:
            path = update.document_path

            # Backup original
            if path.exists():
                backup_path = path.with_suffix(path.suffix + ".bak")
                backup_path.write_text(update.original_content or "")

            # Apply update
            if update.update_type == UpdateType.APPEND:
                with open(path, 'a') as f:
                    f.write("\n" + update.content)
            elif update.update_type == UpdateType.PREPEND:
                original = path.read_text() if path.exists() else ""
                path.write_text(update.content + "\n" + original)
            elif update.update_type == UpdateType.REPLACE:
                path.write_text(update.content)

            update.applied = True
            self._applied_updates.append(update)
            self._daily_update_count += 1

            # Remove from pending
            self._pending_updates.pop(update.id, None)

            # Audit log
            if self.audit:
                await self.audit.log_event(
                    action="self_improvement_applied",
                    details={
                        "update_id": update.id,
                        "document": str(update.document_path),
                        "type": update.update_type.value,
                        "reason": update.reason,
                        "approved_by": update.approved_by,
                    }
                )

            logger.info(f"✅ Applied update {update.id} to {path.name}")

        except Exception as e:
            logger.error(f"Failed to apply update {update.id}: {e}")
            raise

    async def add_learning(
        self,
        content: str,
        category: str = "General",
        source: str = "experience",
    ):
        """
        Add a learning to the learnings document (auto-approved).

        This is the primary way ATLAS records what it learns.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        formatted = f"\n## {timestamp} | {category}\n**Source**: {source}\n\n{content}\n\n---\n"

        await self.propose_update(
            document_path=Path("memory/learnings.md"),
            content=formatted,
            update_type=UpdateType.APPEND,
            reason=f"New learning from {source}",
            source=source,
        )

    async def propose_prompt_improvement(
        self,
        target_section: str,
        current_text: str,
        improved_text: str,
        reason: str,
        evidence: List[str] = None,
    ):
        """
        Propose an improvement to CLAUDE.md or other core prompts.

        This always requires approval.
        """
        content = f"""
## Proposed Improvement to: {target_section}

### Reason
{reason}

### Evidence
{chr(10).join(f'- {e}' for e in (evidence or ['No specific evidence provided']))}

### Current
```
{current_text}
```

### Proposed
```
{improved_text}
```
"""

        await self.propose_update(
            document_path=Path("CLAUDE.md"),
            content=improved_text,
            update_type=UpdateType.SECTION,
            reason=reason,
            source="prompt_optimization",
        )

    async def record_win(
        self,
        description: str,
        what_worked: str,
        metrics: Dict[str, Any] = None,
    ):
        """
        Record a successful outcome to learn from.

        Wins are automatically added to learnings and can inform
        future prompt improvements.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        content = f"""
## {timestamp} | WIN 🎉
**Description**: {description}

**What Worked**:
{what_worked}

**Metrics**: {json.dumps(metrics or {}, indent=2)}
"""

        await self.add_learning(
            content=content,
            category="WIN",
            source="outcome_tracking"
        )

    async def record_failure(
        self,
        description: str,
        what_failed: str,
        root_cause: str,
        prevention: str,
    ):
        """
        Record a failure to learn from and prevent repetition.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        content = f"""
## {timestamp} | FAILURE ANALYSIS ⚠️
**Description**: {description}

**What Failed**:
{what_failed}

**Root Cause**:
{root_cause}

**Prevention**:
{prevention}
"""

        await self.add_learning(
            content=content,
            category="FAILURE_ANALYSIS",
            source="outcome_tracking"
        )

    def get_pending_updates(self) -> List[DocumentUpdate]:
        """Get all pending updates awaiting approval"""
        return list(self._pending_updates.values())

    def get_applied_updates(self, limit: int = 50) -> List[DocumentUpdate]:
        """Get recently applied updates"""
        return self._applied_updates[-limit:]

    async def approve_pending(self, update_id: str, approved_by: str = "operator"):
        """Manually approve a pending update"""
        update = self._pending_updates.get(update_id)
        if not update:
            raise ValueError(f"Update {update_id} not found in pending")

        update.approved = True
        update.approved_at = time.time()
        update.approved_by = approved_by

        await self._apply_update(update)

    async def reject_pending(self, update_id: str, reason: str = ""):
        """Reject a pending update"""
        update = self._pending_updates.pop(update_id, None)
        if update:
            update.approved = False
            update.metadata["rejection_reason"] = reason
            logger.info(f"Rejected update {update_id}: {reason}")

            if self.audit:
                await self.audit.log_event(
                    action="self_improvement_rejected",
                    details={"update_id": update_id, "reason": reason}
                )
