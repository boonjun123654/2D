import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, g
)
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash

# 确保 models.py 里包含 db = SQLAlchemy()，以及下列模型
from models import db, Bet2D, WinningRecord2D, Agent  # 需要提供 Agent 模型

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")
MARKETS = ["M", "P", "T", "S", "B", "K", "W", "H", "E"]


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
    y = int(code[0:4])
    m = int(code[4:6])
    d = int(code[6:8])
    h = int(code[9:11])
    return datetime(y, m, d, h, 0, tzinfo=MY_TZ)


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
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "pool_recycle": 300,
        },
    )
    db.init_app(app)

    # ------------- 简单会话/权限 -------------
    def login_required(f):
        @wraps(f)
        def _wrap(*a, **kw):
            if not session.get("role"):
                return redirect(url_for("login", next=request.path))
            return f(*a, **kw)
        return _wrap

    def admin_required(f):
        @wraps(f)
        def _wrap(*a, **kw):
            if session.get("role") != "admin":
                flash("需要管理员权限", "error")
                return redirect(url_for("login"))
            return f(*a, **kw)
        return _wrap

    @app.before_request
    def load_current_user():
        g.role = session.get("role")          # 'admin' | 'agent' | None
        g.user_id = session.get("user_id")    # 代理 id
        g.username = session.get("username")

    # -------------- 首页/健康检查 --------------
    @app.get("/")
    def index():
        if not session.get("role"):
            return redirect(url_for("login"))
        return redirect(url_for("bet_2d_view"))

    @app.get("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True, "tz": str(MY_TZ)}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    # -------------- 登录/退出 --------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""

            # 管理员（Render 环境变量）
            admin_id = os.environ.get("ADMIN_ID", "")
            admin_hash = os.environ.get("ADMIN_PWHASH", "")
            if username == admin_id and admin_hash and check_password_hash(admin_hash, password):
                session.clear()
                session.update({"role": "admin", "user_id": None, "username": username})
                flash("管理员登录成功", "ok")
                return redirect(request.args.get("next") or url_for("bet_2d_view"))

            # 代理（数据库）
            agent = Agent.query.filter_by(username=username, is_active=True).first()
            if agent and check_password_hash(agent.password_hash, password):
                session.clear()
                session.update({"role": "agent", "user_id": agent.id, "username": agent.username})
                flash("登录成功", "ok")
                return redirect(request.args.get("next") or url_for("bet_2d_view"))

            flash("用户名或密码错误", "error")

        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        flash("已退出登录", "ok")
        return redirect(url_for("login"))

    # -------------- 代理管理（管理员） --------------
    @app.route("/agents", methods=["GET", "POST"])
    @admin_required
    def agents_admin():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if not username or not password:
                flash("用户名与密码必填", "error")
                return redirect(url_for("agents_admin"))
            if Agent.query.filter_by(username=username).first():
                flash("用户名已存在", "error")
                return redirect(url_for("agents_admin"))

            agent = Agent(
                username=username,
                password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
                is_active=True,
            )
            db.session.add(agent)
            db.session.commit()
            flash("已创建代理", "ok")
            return redirect(url_for("agents_admin"))

        agents = Agent.query.order_by(Agent.id.desc()).all()
        return render_template("agents.html", agents=agents)

    @app.post("/agents/<int:agent_id>/toggle")
    @admin_required
    def agent_toggle(agent_id):
        agent = Agent.query.get_or_404(agent_id)
        agent.is_active = not agent.is_active
        db.session.commit()
        flash("状态已切换", "ok")
        return redirect(url_for("agents_admin"))

    @app.post("/agents/<int:agent_id>/reset")
    @admin_required
    def agent_reset(agent_id):
        agent = Agent.query.get_or_404(agent_id)
        new_pwd = request.form.get("new_password") or ""
        if not new_pwd:
            flash("新密码不能为空", "error")
        else:
            agent.password_hash = generate_password_hash(new_pwd, method="pbkdf2:sha256")
            db.session.commit()
            flash("密码已重置", "ok")
        return redirect(url_for("agents_admin"))

    # -------------- 下注页 --------------
    @app.route("/2d/bet", methods=["GET", "POST"])
    @login_required
    def bet_2d_view():
        # 代理：固定自己的 id；管理员：可从表单选择（未传则 1）
        if g.role == "agent" and g.user_id:
            agent_id = int(g.user_id)
        else:
            # 管理员页面可以在表单里提供 <select name="agent_id">
            agent_id = int(request.form.get("agent_id", "0") or 0) or 1

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

                n1 = to_amt("N1", i)
                n = to_amt("N", i)
                bg = to_amt("BIG", i)
                sm = to_amt("SMALL", i)
                od = to_amt("ODD", i)
                ev = to_amt("EVEN", i)
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

                    # 注单对应的锁定时间（当日 HH:49）
                    lock_at = parse_code_to_hour(code).replace(minute=49, second=0, microsecond=0)

                    for m in markets_sel:
                        db.session.add(Bet2D(
                            order_code=order_code,
                            agent_id=agent_id,
                            market=m,
                            code=code,
                            number=number,
                            amount_n1=n1,
                            amount_n=n,
                            amount_b=bg,
                            amount_s=sm,
                            amount_ds=od,
                            amount_ss=ev,
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
        return render_template(
            "bet_2d.html",
            date=date_str,
            slots=slots,
            markets=MARKETS
        )

    # -------------- 当日注单 --------------
    @app.get("/2d/history")
    @login_required
    def history_2d_view():
        today = datetime.now(MY_TZ).strftime("%Y%m%d")
        rows = (
            Bet2D.query
            .filter(Bet2D.code.like(f"{today}/%"))
            .order_by(Bet2D.created_at.desc())
            .all()
        )
        return render_template("history_2d.html", rows=rows, today=today)

    # -------------- 查看中奖 --------------
    @app.get("/2d/winning")
    @login_required
    def winning_2d_view():
        date_str = request.args.get('date') or datetime.now(MY_TZ).strftime("%Y-%m-%d")
        y, m, d = map(int, date_str.split('-'))
        prefix = f"{y:04d}{m:02d}{d:02d}"
        records = (
            WinningRecord2D.query
            .filter(WinningRecord2D.code.like(f"{prefix}/%"))
            .order_by(WinningRecord2D.code.desc(), WinningRecord2D.market.asc())
            .all()
        )
        return render_template("winning_2d.html", records=records, date=date_str)

    return app


# 供 gunicorn 使用：app:app
app = create_app()
