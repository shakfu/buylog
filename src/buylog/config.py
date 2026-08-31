#!/usr/bin/env python3
"""Configuration module for buylog application"""

import os
import logging
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """
    Turn on foreign-key enforcement for every SQLite connection.

    SQLite disables it by default, which lets deletes leave rows pointing at
    entities that no longer exist.
    """
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Config:
    """Application configuration"""

    # Environment
    ENV = os.getenv("BUYER_ENV", "development")

    # Database configuration
    DB_PATH = Path(
        os.getenv("BUYER_DB_PATH", str(Path.home() / ".buylog" / "buylog.db"))
    )
    DB_URL = f"sqlite:///{DB_PATH}"

    # Logging configuration
    LOG_LEVEL = os.getenv("BUYER_LOG_LEVEL", "INFO")
    LOG_PATH = Path(
        os.getenv("BUYER_LOG_PATH", str(Path.home() / ".buylog" / "buylog.log"))
    )

    # Ensure database directory exists
    @classmethod
    def ensure_db_directory(cls):
        """Create database directory if it doesn't exist"""
        cls.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_engine(cls):
        """Get SQLAlchemy engine with proper configuration"""
        cls.ensure_db_directory()
        echo = cls.ENV == "development" and cls.LOG_LEVEL == "DEBUG"
        return create_engine(cls.DB_URL, echo=echo)

    @classmethod
    def get_session_maker(cls):
        """Get SQLAlchemy session maker"""
        engine = cls.get_engine()
        return sessionmaker(bind=engine)

    @classmethod
    def get_db_path(cls) -> Path:
        """Get the database file path"""
        return cls.DB_PATH

    @classmethod
    def setup_logging(cls):
        """Setup application logging"""
        cls.ensure_db_directory()

        # Create logger
        logger = logging.getLogger("buylog")
        logger.setLevel(getattr(logging, cls.LOG_LEVEL))

        # Clear existing handlers
        logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, cls.LOG_LEVEL))
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # File handler
        if cls.ENV == "production":
            file_handler = logging.FileHandler(cls.LOG_PATH)
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

        return logger


# Development configuration
class DevelopmentConfig(Config):
    """Development environment configuration"""

    ENV = "development"
    LOG_LEVEL = "DEBUG"


# Production configuration
class ProductionConfig(Config):
    """Production environment configuration"""

    ENV = "production"
    LOG_LEVEL = "INFO"


# Testing configuration
class TestingConfig(Config):
    """Testing environment configuration"""

    ENV = "testing"
    LOG_LEVEL = "DEBUG"
    DB_PATH = Path(":memory:")  # Use in-memory database for tests
    DB_URL = "sqlite:///:memory:"


# Configuration mapping
config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(env=None):
    """Get configuration for specified environment"""
    if env is None:
        env = os.getenv("BUYER_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
