from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Case(db.Model):
    __tablename__ = 'cases'
    id = db.Column(db.Integer, primary_key=True)
    case_number = db.Column(db.String(100), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    subscriber_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    cdr_records = db.relationship('CDRRecord', backref='case', lazy=True, cascade='all, delete-orphan')

class CDRRecord(db.Model):
    __tablename__ = 'cdr_records'
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=False)
    calling_number = db.Column(db.String(50))
    called_number = db.Column(db.String(50), nullable=False)
    call_type = db.Column(db.String(20))   # VOICE / SMS
    direction = db.Column(db.String(20), default='OUTGOING')
    call_datetime = db.Column(db.DateTime, nullable=False)
    duration_seconds = db.Column(db.Integer, default=0)