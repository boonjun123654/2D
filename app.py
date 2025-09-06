import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from flask import Flask, render_template, request, redirect, session, url_for, flash
from sqlalchemy import text
from models import db, Bet2D, WinningRecord2D, DrawResult

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")

MARKETS = ["M","P","T","S","H","E","B","K","W"]  # 你用哪些就保留哪些

def _fix_db_url(url: str) -> str:
    # Render 会给 postgres:// 前缀，这里转换成 SQLAlchemy 驱动前缀
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    return url

def next_slot_code(now: datetime | None = None) -> str:
    """返回'下一期'的期号 YYYYMMDD/HH50。
       规则：若当前分钟 >=49，则用下一小时；否则用当小时。
    """
    now = now or datetime.now(MY_TZ)
    base = now
    if now.minute >= 49:
        base = (now + timedelta(hours=1)).replace(minute=now.minute, second=now.second)
    return base.strftime("%Y%m%d") + f"/{base.hour:02d}50"

def is_locked_for_code(target_code: str, now: datetime | None = None) -> bool:
    """判断某个 code 是否已过锁注时间（:49）。"""
    now = now or datetime.now(MY_TZ)
    # 把 code 解析回该小时
    # code 形如 20250901/1950
    dt_hour = datetime(
        int(target_code[0:4]), int(target_code[4:6]), int(target_code[6:8]),
        int(target_code[9:11]), 0, tzinfo=MY_TZ
    )
    lock_time = dt_hour.replace(minute=49, second=0, microsecond=0)
    return now >= lock_time

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
        return render_template("home.html")

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True, "tz": str(MY_TZ)}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    # ===== 下注页 =====
    @app.route("/2d/bet", methods=["GET", "POST"])
    def bet_2d_view():
        # DEMO：没有登录系统，agent_id 固定为 1；若你有登录，可从 session 拿。
        agent_id = 1

        code = next_slot_code()
        if request.method == "POST":
            market = request.form.get("market", "M").strip()[:1]
            if market not in MARKETS:
                flash("无效的市场", "error")
                return redirect(url_for("bet_2d_view"))

            # 强制使用服务器计算的下一期 code，避免用户绕过锁注
            target_code = code

            if is_locked_for_code(target_code):
                flash("已过锁注时间，自动切换到下一期再试。", "error")
                return redirect(url_for("bet_2d_view"))

            # 多行字段（以 [] 结尾）
            numbers = request.form.getlist("number[]")
            n1s    = request.form.getlist("amount_n1[]")
            ns     = request.form.getlist("amount_n[]")
            bs     = request.form.getlist("amount_b[]")
            ss     = request.form.getlist("amount_s[]")
            dss    = request.form.getlist("amount_ds[]")
            sss    = request.form.getlist("amount_ss[]")

            created = 0
            for i in range(len(numbers)):
                num_raw = (numbers[i] or "").strip()
                if num_raw == "":
                    continue
                # 标准化两位
                if not num_raw.isdigit():
                    flash(f"第 {i+1} 行号码非法：{num_raw}", "error")
                    continue
                num_int = int(num_raw)
                if num_int < 0 or num_int > 99:
                    flash(f"第 {i+1} 行号码越界：{num_raw}", "error")
                    continue
                num = f"{num_int:02d}"

                def to_amt(arr, idx):
                    try:
                        v = Decimal((arr[idx] or "0").strip() or "0")
                        return Decimal("0.00") if v <= 0 else v
                    except (InvalidOperation, IndexError):
                        return Decimal("0.00")

                a_n1 = to_amt(n1s, i)
                a_n  = to_amt(ns, i)
                a_b  = to_amt(bs, i)
                a_s  = to_amt(ss, i)
                a_ds = to_amt(dss, i)
                a_ss = to_amt(sss, i)

                if (a_n1 + a_n + a_b + a_s + a_ds + a_ss) == 0:
                    # 全为 0 的行不入库
                    continue

                order_code = datetime.now(MY_TZ).strftime("%y%m%d") + "/" + f"{int(datetime.now(MY_TZ).timestamp())%10000:04d}"

                b = Bet2D(
                    order_code=order_code,
                    agent_id=agent_id,
                    market=market,
                    code=target_code,
                    number=num,
                    amount_n1=a_n1, amount_n=a_n,
                    amount_b=a_b, amount_s=a_s,
                    amount_ds=a_ds, amount_ss=a_ss,
                    status="active"
                )
                db.session.add(b)
                created += 1

            if created > 0:
                db.session.commit()
                flash(f"已提交 {created} 条注单，期号 {target_code}（{market}）。", "ok")
                return redirect(url_for("history_2d_view"))
            else:
                flash("没有有效的行被提交。", "error")
                return redirect(url_for("bet_2d_view"))

        # GET
        return render_template("bet_2d.html",
                               code=code,
                               markets=MARKETS,
                               now=datetime.now(MY_TZ))

    # ===== 查单页（当天） =====
    @app.get("/2d/history")
    def history_2d_view():
        # DEMO：没有登录，展示当天所有
        today = datetime.now(MY_TZ).strftime("%Y%m%d")
        q = (Bet2D.query
             .filter(Bet2D.code.like(f"{today}/%"))
             .order_by(Bet2D.created_at.desc()))
        rows = q.all()
        return render_template("history_2d.html", rows=rows, today=today)

    # ===== 查看中奖（原有） =====
    @app.get("/2d/winning")
    def winning_2d_view():
        date_str = request.args.get('date')
        if not date_str:
            date_str = datetime.now(MY_TZ).strftime("%Y-%m-%d")
        y, m, d = map(int, date_str.split('-'))
        prefix = f"{y:04d}{m:02d}{d:02d}"
        q = (WinningRecord2D.query
             .filter(WinningRecord2D.code.like(f"{prefix}/%"))
             .order_by(WinningRecord2D.code.desc(), WinningRecord2D.market.asc()))
        records = q.all()
        return render_template("winning_2d.html", records=records, date=date_str)

    return app

app = create_app()
