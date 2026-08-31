#!/usr/bin/env python3
"""
Audit logging system for tracking sensitive operations.

This module provides comprehensive audit logging for:
- Entity creation, updates, and deletions
- User authentication attempts
- Data access patterns
- System configuration changes
"""

import datetime
import logging
from enum import Enum
from typing import Optional
from pathlib import Path

from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import Session

from .models import Base

# Setup audit logger (separate from application logger)
audit_logger = logging.getLogger("buylog.audit")
audit_logger.setLevel(logging.INFO)

# Path of the audit log file; the handler is attached lazily on first write so
# that importing this module has no filesystem side effects.
audit_log_path = Path.home() / ".buylog" / "audit.log"

_file_handler_attached = False


def _ensure_file_handler() -> None:
    """Attach the audit file handler on first use."""
    global _file_handler_attached
    if _file_handler_attached:
        return
    _file_handler_attached = True
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(audit_log_path)
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        audit_logger.addHandler(handler)
    except OSError as e:  # pragma: no cover - unwritable home directory
        audit_logger.warning(f"Audit file logging disabled: {e}")


class AuditAction(str, Enum):
    """Enumeration of auditable actions"""

    # Entity operations
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"

    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"

    # System
    CONFIG_CHANGE = "config_change"
    EXPORT = "export"
    IMPORT = "import"


class AuditLog(Base):
    """
    Database model for audit log entries.

    Stores audit trail in database for querying and compliance.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False, index=True
    )
    action = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True, index=True)
    entity_id = Column(Integer, nullable=True)
    user = Column(String(100), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(Text, nullable=True)
    success = Column(Integer, default=1, nullable=False)  # 1=success, 0=failure

    def __repr__(self):
        return (
            f"<AuditLog(timestamp={self.timestamp}, action={self.action}, "
            f"entity_type={self.entity_type}, user={self.user})>"
        )


class AuditService:
    """Service for audit logging operations"""

    @staticmethod
    def log_action(
        action: AuditAction,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        user: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[str] = None,
        success: bool = True,
        session: Optional[Session] = None,
    ) -> None:
        """
        Log an auditable action.

        Args:
            action: Type of action performed
            entity_type: Type of entity affected (e.g., "brand", "product")
            entity_id: ID of affected entity
            user: Username or identifier of user performing action
            ip_address: IP address of client
            details: Additional details about the action
            success: Whether action succeeded
            session: Optional database session for persistent logging

        Example:
            >>> AuditService.log_action(
            ...     AuditAction.CREATE,
            ...     entity_type="brand",
            ...     entity_id=123,
            ...     user="admin",
            ...     details="Created brand 'Apple'"
            ... )
        """
        # Log to file
        log_message = (
            f"Action={action.value}, EntityType={entity_type}, EntityID={entity_id}, "
            f"User={user}, IP={ip_address}, Success={success}"
        )
        if details:
            log_message += f", Details={details}"

        _ensure_file_handler()
        if success:
            audit_logger.info(log_message)
        else:
            audit_logger.warning(log_message)

        # Log to database if session provided
        if session:
            try:
                audit_entry = AuditLog(
                    action=action.value,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    user=user or "anonymous",
                    ip_address=ip_address,
                    details=details,
                    success=1 if success else 0,
                )
                session.add(audit_entry)
                session.commit()
            except Exception as e:
                # Don't let audit logging failure break the application. The
                # failed flush leaves the session in a rolled-back state, so
                # roll back explicitly: otherwise every later use of this
                # session raises PendingRollbackError.
                try:
                    session.rollback()
                except Exception:  # pragma: no cover - rollback should not fail
                    pass
                audit_logger.error(f"Failed to write audit log to database: {e}")

    @staticmethod
    def log_create(
        entity_type: str,
        entity_id: int,
        entity_name: str,
        user: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """
        Log entity creation.

        Args:
            entity_type: Type of entity (e.g., "brand")
            entity_id: ID of created entity
            entity_name: Name of created entity
            user: Username
            session: Database session
        """
        AuditService.log_action(
            AuditAction.CREATE,
            entity_type=entity_type,
            entity_id=entity_id,
            user=user,
            details=f"Created {entity_type}: {entity_name}",
            session=session,
        )

    @staticmethod
    def log_update(
        entity_type: str,
        entity_id: int,
        old_value: str,
        new_value: str,
        user: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """
        Log entity update.

        Args:
            entity_type: Type of entity
            entity_id: ID of updated entity
            old_value: Previous value
            new_value: New value
            user: Username
            session: Database session
        """
        AuditService.log_action(
            AuditAction.UPDATE,
            entity_type=entity_type,
            entity_id=entity_id,
            user=user,
            details=f"Updated {entity_type} from '{old_value}' to '{new_value}'",
            session=session,
        )

    @staticmethod
    def log_delete(
        entity_type: str,
        entity_id: int,
        entity_name: str,
        user: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """
        Log entity deletion.

        Args:
            entity_type: Type of entity
            entity_id: ID of deleted entity
            entity_name: Name of deleted entity
            user: Username
            session: Database session
        """
        AuditService.log_action(
            AuditAction.DELETE,
            entity_type=entity_type,
            entity_id=entity_id,
            user=user,
            details=f"Deleted {entity_type}: {entity_name}",
            session=session,
        )

    @staticmethod
    def log_login_attempt(
        username: str,
        success: bool,
        ip_address: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """
        Log authentication attempt.

        Args:
            username: Username attempting to log in
            success: Whether login succeeded
            ip_address: IP address of client
            reason: Reason for failure (if applicable)
        """
        action = AuditAction.LOGIN_SUCCESS if success else AuditAction.LOGIN_FAILURE
        details = f"Login attempt for user '{username}'"
        if not success and reason:
            details += f": {reason}"

        AuditService.log_action(
            action,
            user=username,
            ip_address=ip_address,
            details=details,
            success=success,
        )

    @staticmethod
    def get_recent_logs(
        session: Session,
        limit: int = 100,
        action: Optional[AuditAction] = None,
        entity_type: Optional[str] = None,
        user: Optional[str] = None,
    ) -> list:
        """
        Retrieve recent audit logs.

        Args:
            session: Database session
            limit: Maximum number of logs to retrieve
            action: Filter by action type
            entity_type: Filter by entity type
            user: Filter by user

        Returns:
            List of AuditLog entries
        """
        query = session.query(AuditLog).order_by(AuditLog.timestamp.desc())

        if action:
            query = query.filter(AuditLog.action == action.value)
        if entity_type:
            query = query.filter(AuditLog.entity_type == entity_type)
        if user:
            query = query.filter(AuditLog.user == user)

        return query.limit(limit).all()

    @staticmethod
    def get_entity_history(session: Session, entity_type: str, entity_id: int) -> list:
        """
        Get complete audit history for a specific entity.

        Args:
            session: Database session
            entity_type: Type of entity
            entity_id: ID of entity

        Returns:
            List of AuditLog entries for the entity
        """
        return (
            session.query(AuditLog)
            .filter(
                AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id
            )
            .order_by(AuditLog.timestamp.asc())
            .all()
        )
