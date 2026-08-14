"""Warehouse read-model, declared with SQLAlchemy rather than the ORM."""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class PickList(Base):
    __tablename__ = "pick_list"
    id = Column(Integer, primary_key=True)
    order_ref = Column(String(32), nullable=False, index=True)
    picked_at = Column(DateTime, nullable=True)


class PickLine(Base):
    __tablename__ = "pick_line"
    id = Column(Integer, primary_key=True)
    pick_list_id = Column(Integer, nullable=False)
    sku = Column(String(64), nullable=False)


class Bin(Base):
    __tablename__ = "bin"
    id = Column(Integer, primary_key=True)
    code = Column(String(16), nullable=False, unique=True)


class BinContents(Base):
    __tablename__ = "bin_contents"
    id = Column(Integer, primary_key=True)
    bin_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
