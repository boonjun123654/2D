import os
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from flask import Flask, render_template, request, redirect, session, url_for
from sqlalchemy import text  # ✅ 用于健康检查
from models import db, Bet2D, WinningRecord2D, DrawResult
from odds_config_2d import ODDS_2D

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _fix_db_url(url: str) -> str:
    # Render 的 DATABASE_URL 可能是 postgres://，需要替换成 postgresql+psycopg2://
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url


def create_app() -> Flask:
    app = Flask(__name__)

    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = _fix_db_url(os.environ.get("DATABASE_URL"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    db.init_app(app)

    @app.get("/")
    def index():
        return redirect(url_for('winning_2d_view'))

    @app.get("/healthz")
    def healthz():
        # 简单健康检查：数据库可连 & 当前时区
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True, "tz": str(MY_TZ)}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    @app.get("/2d/winning")
    def winning_2d_view():
        # 查询参数：?date=YYYY-MM-DD（展示当天所有期）
        date_str = request.args.get('date')
        if not date_str:
            # 默认今天
            date_str = datetime.now(MY_TZ).strftime("%Y-%m-%d")

        y, m, d = map(int, date_str.split('-'))
        prefix = f"{y:04d}{m:02d}{d:02d}"

        q = (WinningRecord2D.query
             .filter(WinningRecord2D.code.like(f"{prefix}/%"))
             .order_by(WinningRecord2D.code.desc(), WinningRecord2D.market.asc()))

        # 如需按登录角色过滤，可在此加 agent_id 条件（此处先展示全量）
        records = q.all()

        return render_template("winning_2d.html", records=records, date=date_str)

    return app


# 供 gunicorn 使用
app = create_app()
