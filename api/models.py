from sqlalchemy import (
    Column, BigInteger, Integer, String, Text, DateTime, Boolean, 
    ForeignKey, Index, CheckConstraint, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy import DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
import os

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    
    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")

class Bookmark(Base):
    __tablename__ = "bookmarks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(
        String(20), 
        nullable=False,
        default="other"
    )
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    author = Column(Text)
    thumbnail_url = Column(Text)
    description = Column(Text)
    note = Column(Text)
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())
    raw = Column(Text, nullable=False, default='{}')
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    __table_args__ = (
        CheckConstraint(
            "platform IN ('youtube','tiktok','instagram','twitter','linkedin','reddit','pinterest','snapchat','facebook','other')",
            name='check_platform_values'
        ),
        Index('idx_bookmarks_platform_created_at', 'platform', 'created_at'),
        Index('idx_bookmarks_raw_gin', 'raw', postgresql_using='gin'),
        Index('idx_bookmarks_user_created', 'user_id', 'created_at'),
    )
    
    user = relationship("User", back_populates="bookmarks")
    youtube_details = relationship("YouTubeDetails", back_populates="bookmark", cascade="all, delete-orphan")

class YouTubeDetails(Base):
    __tablename__ = "youtube_details"
    
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True)
    video_id = Column(String(20), nullable=False, unique=True)
    channel_id = Column(String(50))
    duration_seconds = Column(Integer)
    view_count = Column(Integer)
    like_count = Column(Integer)
    tags = Column(Text)
    extra = Column(Text, nullable=False, default='{}')
    
    __table_args__ = (
        Index('idx_youtube_video_id', 'video_id', unique=True),
    )
    
    bookmark = relationship("Bookmark", back_populates="youtube_details")

class Caption(Base):
    __tablename__ = "captions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(20), nullable=False)
    lang = Column(String(10), nullable=False, default='en')
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    
    __table_args__ = (
        CheckConstraint(
            "source IN ('whisper','api','manual')",
            name='check_caption_source'
        ),
    )

class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    bookmark_id = Column(Integer, ForeignKey("bookmarks.id", ondelete="CASCADE"), nullable=False)
    platform_author = Column(String(255))
    text = Column(Text, nullable=False)
    like_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    raw = Column(Text, nullable=False, default='{}')
    
    __table_args__ = (
        Index('idx_comments_bookmark_created', 'bookmark_id', 'created_at'),
    ) 