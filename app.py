import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text
from models import db, Bet2D, WinningRecord2D

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")
MARKETS = ["M","P","T","S","B","K","W","H","E"]  # 按你8~9个市场，需要几个留几个

def _fix_db_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url

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
            "hour": h,                # 新增：小时，用于表头显示 9/10/.../23
            "label": f"{h:02d}:50",
            "locked": locked
        })
    return slots

def next_slot_code(now: datetime | None = None) -> str:
    now = now or datetime.now(MY_TZ)
    base = now if now.minute < 49 else now + timedelta(hours=1)
    return base.strftime("%Y%m%d") + f"/{base.hour:02d}50"

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
        # 有需要可换成 render_template("home.html")
        return redirect(url_for('bet_2d_view'))

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True, "tz": str(MY_TZ)}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

@app.route("/2d/bet", methods=["GET","POST"])
def bet_2d_view():
    agent_id = 1  # DEMO

    # 日期：默认今天；仍可用 ?date=YYYY-MM-DD 传入，但页面不展示选择器
    date_str = request.args.get("date")
    if not date_str:
        date_str = datetime.now(MY_TZ).strftime("%Y-%m-%d")
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now(MY_TZ).date()
        date_str = day.strftime("%Y-%m-%d")

    slots = list_slots_for_day(day)  # [{code,hour,label,locked}]

    if request.method == "POST":
        # 隐藏域传回日期
        form_date = request.form.get("date", date_str)
        try:
            submit_day = datetime.strptime(form_date, "%Y-%m-%d").date()
        except:
            submit_day = day

        numbers = request.form.getlist("number[]")
        a_n1    = request.form.getlist("amount_n1[]")
        a_n     = request.form.getlist("amount_n[]")
        a_b     = request.form.getlist("amount_big[]")
        a_s     = request.form.getlist("amount_small[]")
        a_o     = request.form.getlist("amount_odd[]")
        a_e     = request.form.getlist("amount_even[]")

        created = 0
        nowts = datetime.now(MY_TZ)

        from decimal import Decimal, InvalidOperation
        def to_amt(arr, idx):
            try:
                v = Decimal((arr[idx] or "0").strip() or "0")
                return Decimal("0.00") if v <= 0 else v
            except (InvalidOperation, IndexError):
                return Decimal("0.00")

        for i in range(len(numbers)):
            num_raw = (numbers[i] or "").strip()
            if not num_raw or not num_raw.isdigit(): continue
            vi = int(num_raw)
            if vi < 0 or vi > 99: continue
            number = f"{vi:02d}"

            n1 = to_amt(a_n1, i); n = to_amt(a_n, i)
            big= to_amt(a_b,  i); sml= to_amt(a_s, i)
            odd= to_amt(a_o,  i); evn= to_amt(a_e, i)
            if (n1+n+big+sml+odd+evn) == 0: continue

            # 该行勾选的时段/市场；若都未勾，则默认“下一期/M”
            slots_sel   = request.form.getlist(f"slot_{i}[]") or [next_slot_code()]
            markets_sel = request.form.getlist(f"market_{i}[]") or ["M"]

            for code in slots_sel:
                if is_locked_for_code(code):  # 过锁点跳过
                    continue
                for m in markets_sel:
                    order_code = nowts.strftime("%y%m%d") + "/" + f"{int(nowts.timestamp())%10000:04d}"
                    db.session.add(Bet2D(
                        order_code=order_code,
                        agent_id=agent_id, market=m, code=code, number=number,
                        amount_n1=n1, amount_n=n, amount_b=big, amount_s=sml,
                        amount_ds=odd, amount_ss=evn, status="active"
                    ))
                    created += 1

        if created > 0:
            db.session.commit()
            flash(f"已提交 {created} 条注单。", "ok")
            return redirect(url_for("history_2d_view"))
        else:
            flash("没有有效行（或所选时间段已过锁注）。", "error")
            return redirect(url_for("bet_2d_view", date=date_str))

    # GET：渲染（不展示“当前时间/日期选择器”）
    return render_template("bet_2d.html",
                           date=date_str,
                           slots=slots,
                           markets=MARKETS)

    # =================== 当日注单（原样） ===================
    @app.get("/2d/history")
    def history_2d_view():
        today = datetime.now(MY_TZ).strftime("%Y%m%d")
        rows = (Bet2D.query
                .filter(Bet2D.code.like(f"{today}/%"))
                .order_by(Bet2D.created_at.desc())
                .all())
        return render_template("history_2d.html", rows=rows, today=today)

    # =================== 查看中奖（原样） ===================
    @app.get("/2d/winning")
    def winning_2d_view():
        date_str = request.args.get('date')
        if not date_str:
            date_str = datetime.now(MY_TZ).strftime("%Y-%m-%d")
        y, m, d = map(int, date_str.split('-'))
        prefix = f"{y:04d}{m:02d}{d:02d}"
        records = (WinningRecord2D.query
                   .filter(WinningRecord2D.code.like(f"{prefix}/%"))
                   .order_by(WinningRecord2D.code.desc(), WinningRecord2D.market.asc())
                   .all())
        return render_template("winning_2d.html", records=records, date=date_str)

    return app

app = create_app()
