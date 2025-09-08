import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

db = SQLAlchemy()

class Bet2D(db.Model):
    __tablename__ = 'bets_2d'
    id = db.Column(db.BigInteger, primary_key=True)
    order_code = db.Column(db.String(16))
    agent_id = db.Column(db.Integer, nullable=False)
    market = db.Column(db.String(16), nullable=False)  # M/P/T/S/H/E/B/K/W
    code = db.Column(db.String(13), nullable=False)   # YYYYMMDD/HHMM
    number = db.Column(db.String(2), nullable=False)  # '00'..'99'

    amount_n1 = db.Column(db.Numeric(12,2), default=0)
    amount_n  = db.Column(db.Numeric(12,2), default=0)
    amount_b  = db.Column(db.Numeric(12,2), default=0)
    amount_s  = db.Column(db.Numeric(12,2), default=0)
    amount_ds = db.Column(db.Numeric(12,2), default=0)
    amount_ss = db.Column(db.Numeric(12,2), default=0)

    status = db.Column(db.String(10), nullable=False, default='active')  # active/locked/delete
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    locked_at  = db.Column(db.DateTime(timezone=True))

class WinningRecord2D(db.Model):
    __tablename__ = 'winning_record_2d'
    id = db.Column(db.BigInteger, primary_key=True)
    bet_id   = db.Column(db.BigInteger, db.ForeignKey('bets_2d.id', ondelete='CASCADE'), nullable=False)
    agent_id = db.Column(db.Integer, nullable=False)
    market   = db.Column(db.String(1), nullable=False)
    code     = db.Column(db.String(13), nullable=False)
    number   = db.Column(db.String(2), nullable=False)

    hit_type = db.Column(db.String(12), nullable=False)   # N1 / N_HEAD / N_SPECIAL / B / S / DS / SS
    stake    = db.Column(db.Numeric(12,2), nullable=False)
    odds     = db.Column(db.Numeric(10,2), nullable=False) # 含本金
    payout   = db.Column(db.Numeric(12,2), nullable=False) # 不含本金

    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

class DrawResult(db.Model):
    __tablename__ = 'draw_results'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)      # YYYYMMDD/HHMM
    market = db.Column(db.String(1), nullable=False)
    head = db.Column(db.String(2), nullable=False)       # '00'..'99'
    specials = db.Column(db.String(20), nullable=False)  # '71,89,30'
    size_type = db.Column(db.String(2))                  # 大/小
    parity_type = db.Column(db.String(2))                # 单/双
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('code', 'market', name='uq_draw_code_market'),
    )

class Agent(db.Model):
    __tablename__ = "agents"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime(timezone=True), server_default=func.now())
