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

# ---- 首页（2D）展示用赔率（不区分市场） ----
CATS_2D = ["N1", "N", "BIG", "SMALL", "ODD", "EVEN"]
ODDS_2D_SIMPLE = {
    "N1": "头奖 1:50 / 特别奖无",
    "N":  "头奖 1:28 / 特别奖 1:7",
    "BIG":   "1:1.9",
    "SMALL": "1:1.9",
    "ODD":   "1:1.9",
    "EVEN":  "1:1.9",
}


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
    y = int(code[0:4]); m = int(code[4:6]); d = int(code[6:8]); h = int(code[9:11])
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
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 300},
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

    # -------------- 首页/健康检查/入口 --------------
    @app.get("/")
    def index():
        return redirect(url_for('home') if session.get('role') else url_for('login'))

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

            # 管理员（Render 环境变量，明文密码）
            admin_id = os.environ.get("ADMIN_ID", "")
            admin_pw = os.environ.get("ADMIN_PASSWORD", "")
            if username == admin_id and admin_pw and password == admin_pw:
                session.clear()
                session.update({"role": "admin", "user_id": None, "username": username})
                flash("管理员登录成功", "ok")
                return redirect(request.args.get("next") or url_for("home"))

            # 代理（数据库，哈希校验）
            agent = Agent.query.filter_by(username=username, is_active=True).first()
            if agent and check_password_hash(agent.password_hash, password):
                session.clear()
                session.update({"role": "agent", "user_id": agent.id, "username": agent.username})
                flash("登录成功", "ok")
                return redirect(request.args.get("next") or url_for("home"))

            flash("用户名或密码错误", "error")

        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        flash("已退出登录", "ok")
        return redirect(url_for("login"))

    # -------------- 首页（赔率展示，仅 2D 简版） --------------
    @app.get("/home")
    @login_required
    def home():
        return render_template(
            "home.html",
            odds2=ODDS_2D_SIMPLE,
            cats2=CATS_2D
        )

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
        # —— 确定本次下注使用的 agent_id
        if g.role == "agent" and g.user_id:
            agent_id = int(g.user_id)
            agents_for_select = None  # 代理不显示选择
        else:
            agent_id = int((request.values.get("agent_id") or "1").strip())
            try:
                agents_for_select = Agent.query.order_by(Agent.username.asc()).all()
            except Exception:
                agents_for_select = []

        date_str = request.args.get("date") or datetime.now(MY_TZ).strftime("%Y-%m-%d")
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            day = datetime.now(MY_TZ).date()
            date_str = day.strftime("%Y-%m-%d")

        slots = list_slots_for_day(day)

        if request.method == "POST":
            # 再次确认 agent_id（代理强制自己，管理员可选）
            if g.role == "agent" and g.user_id:
                agent_id = int(g.user_id)
            else:
                agent_id = int((request.form.get("agent_id") or "1").strip())

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
                n  = to_amt("N",  i)
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

                # —— 选中的市场：合并为字符串并按固定顺序排序（保证 MPT、而不是随机顺序）
                markets_sel = [m for m in MARKETS if request.form.get(f"market{i}_{m}")]
                if not markets_sel:
                    markets_sel = ["M"]
                market_str = "".join([m for m in MARKETS if m in markets_sel])  # 例如 "MPT"

                for code in slots_sel:
                    if is_locked_for_code(code):
                        continue

                    ts = datetime.now(MY_TZ)
                    order_code = ts.strftime("%y%m%d/%H%M%S") + f"{int(ts.microsecond/1000):03d}"

                    lock_at = parse_code_to_hour(code).replace(minute=49, second=0, microsecond=0)

                    # ✅ 一次仅写入一条 —— market 直接存 "MPT"
                    db.session.add(Bet2D(
                        order_code=order_code,
                        agent_id=int(agent_id or 0),   # ✅ 直接保存“代理ID”本身
                        market=market_str,             # ✅ 合并后的市场字符串
                        code=code,
                        number=number,
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
                    # 成功后回到本页，带 success=1 —— 前端据此弹窗；管理员保留 agent_id
                    if g.role == "admin":
                        return redirect(url_for("bet_2d_view", date=form_date, success=1, agent_id=agent_id))
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
            markets=MARKETS,
            agents=agents_for_select,           # 仅管理员非空
            selected_agent_id=agent_id          # 仅管理员有用
        )

    # -------------- 历史记录（按日期） --------------
    @app.get("/2d/history")
    @login_required
    def history_2d_view():
        date_str = request.args.get("date") or datetime.now(MY_TZ).strftime("%Y-%m-%d")
        try:
            y, m, d = map(int, date_str.split("-"))
        except Exception:
            dt = datetime.now(MY_TZ).date()
            date_str = dt.strftime("%Y-%m-%d")
            y, m, d = dt.year, dt.month, dt.day

        prefix = f"{y:04d}{m:02d}{d:02d}"

        rows = (
            Bet2D.query
            .filter(
                Bet2D.code.like(f"{prefix}/%"),
                Bet2D.status != "delete"        # ✅ 排除已删除
            )
            .order_by(Bet2D.created_at.desc())
            .all()
        )

        def _f(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        rows_js = [{
            "order_code": r.order_code or "",
            "agent_id":   r.agent_id,
            "market":     r.market or "",
            "code":       r.code or "",
            "number":     r.number or "",
            "amount_n1":  _f(r.amount_n1 or 0),
            "amount_n":   _f(r.amount_n  or 0),
            "amount_b":   _f(r.amount_b  or 0),
            "amount_s":   _f(r.amount_s  or 0),
            "amount_ds":  _f(r.amount_ds or 0),
            "amount_ss":  _f(r.amount_ss or 0),
            "status":     r.status or "active",
            "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows]

        return render_template("history_2d.html", date=date_str, rows_js=rows_js)

    @app.post("/2d/history/delete")
    @login_required
    def history_2d_delete():
        """
        将指定 order_code 的订单标记为 delete。
        - 管理员可删除任意订单
        - 代理只能删除自己的订单
        前端以 x-www-form-urlencoded 或 JSON 发送 {order_code: "..."}
        """
        # 支持 form 和 json 两种提交
        data = request.get_json(silent=True) or {}
        order_code = (request.form.get("order_code")
                      or data.get("order_code")
                      or "").strip()

        if not order_code:
            return {"ok": False, "error": "缺少 order_code"}, 400

        # 查询范围：管理员不限；代理仅限自己的
        q = Bet2D.query.filter(
            Bet2D.order_code == order_code,
            Bet2D.status != "delete"
        )
        if g.role == "agent" and g.user_id:
            q = q.filter(Bet2D.agent_id == int(g.user_id))

        rows = q.all()
        if not rows:
            return {"ok": False, "error": "未找到该订单或无权限"}, 404

        try:
            for r in rows:
                r.status = "delete"
            db.session.commit()
            return {"ok": True, "count": len(rows)}
        except Exception as e:
            db.session.rollback()
            return {"ok": False, "error": str(e)}, 500

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
