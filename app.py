import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation

from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text

from models import db, Bet2D, WinningRecord2D  # 确保 models.py 里有 db = SQLAlchemy()

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")
MARKETS = ["M","P","T","S","B","K","W","H","E"]

def _fix_db_url(url: str) -> str:
    """Render 常给 postgres:// 前缀；转换为 SQLAlchemy 需要的前缀。"""
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url

# ---------- 工具函数 ----------
def parse_code_to_hour(code: str) -> datetime:
    # 20250906/1950 -> 2025-09-06 19:00 +08:00
    y=int(code[0:4]); m=int(code[4:6]); d=int(code[6:8]); h=int(code[9:11])
    return datetime(y,m,d,h,0,tzinfo=MY_TZ)

def is_locked_for_code(code: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(MY_TZ)
    lock_time = parse_code_to_hour(code).replace(minute=49, second=0, microsecond=0)
    return now >= lock_time

def list_slots_for_day(day: date) -> list[dict]:
    """返回当日 09:50~23:50 的期号列表：[{code,hour,label,locked}]"""
    slots = []
    for h in range(9, 24):  # 9..23
        code = day.strftime("%Y%m%d") + f"/{h:02d}50"
        locked = is_locked_for_code(code)
        slots.append({
            "code": code,
            "hour": h,
            "label": f"{h:02d}:50",
            "locked": locked
        })
    return slots

def next_slot_code(now: datetime | None = None) -> str:
    """根据当前时间给出下一期号：
       <09:00 -> 当天 09:50；>=23:49 -> 次日 09:50；否则下一小时 :50。"""
    now = now or datetime.now(MY_TZ)
    day = now.date()
    hour = now.hour + (1 if now.minute >= 49 else 0)
    if hour < 9:
        hour = 9
    elif hour > 23:
        day = day + timedelta(days=1)
        hour = 9
    return f"{day.strftime('%Y%m%d')}/{hour:02d}50"

# ========================= 应用工厂 =========================
def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = _fix_db_url(os.environ.get("DATABASE_URL"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # 连接更稳
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "pool_recycle": 300,
        },
    )
    db.init_app(app)

    @app.get("/")
    def index():
        return redirect(url_for('bet_2d_view'))

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True, "tz": str(MY_TZ)}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    # -------------- 下注页 --------------
    @app.route("/2d/bet", methods=["GET","POST"])
    def bet_2d_view():
        agent_id = 1  # DEMO：没有登录体系，先写死 1

        date_str = request.args.get("date") or datetime.now(MY_TZ).strftime("%Y-%m-%d")
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            day = datetime.now(MY_TZ).date()
            date_str = day.strftime("%Y-%m-%d")

        slots = list_slots_for_day(day)

        if request.method == "POST":
            form_date = request.form.get("date", date_str)
            try:
                _ = datetime.strptime(form_date, "%Y-%m-%d").date()
            except Exception:
                form_date = date_str

            created = 0
            slots_today = list_slots_for_day(day)  # 用于从索引还原 code

            def to_amt(name: str, i: int) -> Decimal:
                try:
                    raw = (request.form.get(f"{name}{i}") or "").strip()
                    v = Decimal(raw or "0")
                    return Decimal("0.00") if v <= 0 else v
                except InvalidOperation:
                    return Decimal("0.00")

            for i in range(1, 13):
                raw_num = (request.form.get(f"number{i}") or "").strip()
                if not raw_num.isdigit():  # 空行或非法
                    continue
                vi = int(raw_num)
                if vi < 0 or vi > 99:
                    continue
                number = f"{vi:02d}"

                n1 = to_amt("N1", i); n  = to_amt("N", i)
                bg = to_amt("BIG", i); sm = to_amt("SMALL", i)
                od = to_amt("ODD", i); ev = to_amt("EVEN", i)
                if (n1 + n + bg + sm + od + ev) == 0:
                    continue

                # 行内选中的时间段：slot{i}_{idx} → code
                slots_sel: list[str] = []
                for idx, slot in enumerate(slots_today):
                    if request.form.get(f"slot{i}_{idx}") and not is_locked_for_code(slot["code"]):
                        slots_sel.append(slot["code"])
                if not slots_sel:
                    slots_sel = [next_slot_code()]  # 没选则默认下一期

                # 行内选中的市场：market{i}_M 等
                markets_sel = [m for m in MARKETS if request.form.get(f"market{i}_{m}")]
                if not markets_sel:
                    markets_sel = ["M"]

                for code in slots_sel:
                    if is_locked_for_code(code):
                        continue
                    ts = datetime.now(MY_TZ)
                    order_code = ts.strftime("%y%m%d/%H%M%S") + f"{int(ts.microsecond/1000):03d}"

                    # 可选：预写入该注单对应的锁定时间（当日 HH:49）
                    lock_at = parse_code_to_hour(code).replace(minute=49, second=0, microsecond=0)

                    for m in markets_sel:
                        db.session.add(Bet2D(
                            order_code=order_code,
                            agent_id=1, market=m, code=code, number=number,
                            amount_n1=n1, amount_n=n,
                            amount_b=bg, amount_s=sm,
                            amount_ds=od, amount_ss=ev,
                            status="active",
                            locked_at=lock_at
                        ))
                        created += 1

            try:
                if created > 0:
                    db.session.commit()
                    flash(f"已提交 {created} 条注单。", "ok")
                    # 成功后回到本页，带 success=1 —— 前端据此弹窗
                    return redirect(url_for("bet_2d_view", date=form_date, success=1))
                else:
                    flash("没有有效行（或所选时间段已过锁注）。", "error")
                    return redirect(url_for("bet_2d_view", date=date_str))
            except Exception as e:
                db.session.rollback()
                flash(f"提交失败：{e}", "error")
                return redirect(url_for("bet_2d_view", date=date_str))

        # GET 渲染
        return render_template("bet_2d.html",
                               date=date_str,
                               slots=slots,
                               markets=MARKETS)

    # -------------- 当日注单 --------------
    @app.get("/2d/history")
    def history_2d_view():
        today = datetime.now(MY_TZ).strftime("%Y%m%d")
        rows = (Bet2D.query
                .filter(Bet2D.code.like(f"{today}/%"))
                .order_by(Bet2D.created_at.desc())
                .all())
        return render_template("history_2d.html", rows=rows, today=today)

    # -------------- 查看中奖 --------------
    @app.get("/2d/winning")
    def winning_2d_view():
        date_str = request.args.get('date') or datetime.now(MY_TZ).strftime("%Y-%m-%d")
        y, m, d = map(int, date_str.split('-'))
        prefix = f"{y:04d}{m:02d}{d:02d}"
        records = (WinningRecord2D.query
                   .filter(WinningRecord2D.code.like(f"{prefix}/%"))
                   .order_by(WinningRecord2D.code.desc(), WinningRecord2D.market.asc())
                   .all())
        return render_template("winning_2d.html", records=records, date=date_str)

    return app

# 供 gunicorn 使用：app:app
app = create_app()
