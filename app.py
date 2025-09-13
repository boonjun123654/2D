import os
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from functools import wraps
from threading import Thread

from flask import (
    Flask, render_template, request, redirect, url_for, flash, session, g, jsonify
)
from sqlalchemy import text, func, and_, cast, Date
from werkzeug.security import generate_password_hash, check_password_hash

# 确保 models.py 里包含 db = SQLAlchemy()，以及下列模型
from models import db, Bet2D, WinningRecord2D, Agent, DrawResult  # 新增 DrawResult

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


# ========================= 自动结算：工具与线程 =========================
# 含本金赔率（与首页文案一致；用于计算不含本金的 payout）
ODDS_SETTLE = {
    'N1': Decimal('50'),
    'N_HEAD': Decimal('28'),
    'N_SPECIAL': Decimal('7'),
    'B': Decimal('1.9'),
    'S': Decimal('1.9'),
    'DS': Decimal('1.9'),
    'SS': Decimal('1.9'),
}

def _payout(stake: Decimal, odds: Decimal) -> Decimal:
    # 赔付为不含本金
    return (Decimal(stake or 0) * (Decimal(odds) - Decimal('1'))).quantize(Decimal('0.01'))

def settle_one(code: str, market: str) -> int:
    """结算单期单市场：把命中写入 WinningRecord2D（应用层幂等）"""
    dr = DrawResult.query.filter_by(code=code, market=market).first()
    if not dr:
        return 0

    head = (dr.head or '').zfill(2)
    specials = {s.strip().zfill(2) for s in (dr.specials or '').split(',') if s.strip()}
    size = dr.size_type   # '大' / '小'
    parity = dr.parity_type  # '单' / '双'

    # 如未提供 size/parity，可根据 head 兜底推断（可选）
    try:
        _hn = int(head)
        if not size:
            size = '大' if _hn >= 50 else '小'
        if not parity:
            parity = '单' if _hn % 2 == 1 else '双'
    except Exception:
        pass

    bets = (Bet2D.query
            .filter(Bet2D.code == code,
                    Bet2D.status.in_(('active', 'locked')),
                    Bet2D.market.contains(market))  # 合并市场字符串里包含该字母
            .all())

    created = 0
    for b in bets:
        def add(hit_type: str, cond: bool, stake):
            nonlocal created
            if not cond:
                return
            stake = Decimal(stake or 0)
            if stake <= 0:
                return
            # 防重复（幂等）
            exists = WinningRecord2D.query.filter_by(
                bet_id=b.id, market=market, code=code, hit_type=hit_type
            ).first()
            if exists:
                return
            odds = ODDS_SETTLE[hit_type]
            db.session.add(WinningRecord2D(
                bet_id=b.id,
                agent_id=b.agent_id,
                market=market,
                code=code,
                number=b.number,
                hit_type=hit_type,
                stake=stake,
                odds=odds,
                payout=_payout(stake, odds),
            ))
            created += 1

        add('N1',        b.amount_n1 and b.number == head,         b.amount_n1)
        add('N_HEAD',    b.amount_n  and b.number == head,         b.amount_n)
        add('N_SPECIAL', b.amount_n  and b.number in specials,     b.amount_n)
        add('B',         b.amount_b  and size == '大',             b.amount_b)
        add('S',         b.amount_s  and size == '小',             b.amount_s)
        add('DS',        b.amount_ds and parity == '单',           b.amount_ds)
        add('SS',        b.amount_ss and parity == '双',           b.amount_ss)

    if created:
        db.session.commit()
    return created

def _target_code_for_53(now: datetime) -> str | None:
    """根据当前时间计算要在 :53 结算的期号（HH:50）。
       :53 及之后 => 当小时 :50；否则 => 上一小时 :50。
       仅 09~23 的小时有效。"""
    if now.minute >= 53:
        tgt = now
    else:
        tgt = now - timedelta(hours=1)
    if tgt.hour < 9 or tgt.hour > 23:
        return None
    return f"{tgt.strftime('%Y%m%d')}/{tgt.hour:02d}50"

def settle_due_draws(now: datetime | None = None) -> int:
    """结算“该在此刻结算”的所有市场（依据 draw_results 是否存在）"""
    now = now or datetime.now(MY_TZ)
    code = _target_code_for_53(now)
    if not code:
        return 0
    drs = DrawResult.query.filter_by(code=code).all()
    total = 0
    for dr in drs:
        total += settle_one(code, dr.market)
    return total

def start_settle_daemon(flask_app: Flask):
    """每小时的 :53 自动结算上一张/当小时的 :50 期"""
    if os.environ.get("ENABLE_SETTLE_DAEMON", "1") not in ("1", "true", "True", "yes"):
        return
    def _loop():
        last_key = None  # 防重复：每小时只跑一次
        while True:
            try:
                now = datetime.now(MY_TZ)
                key = now.strftime("%Y%m%d%H")
                if now.minute == 53 and key != last_key:
                    with flask_app.app_context():
                        n = settle_due_draws(now)
                        flask_app.logger.info(f"[auto-settle] {now.isoformat()} -> created={n}")
                    last_key = key
            except Exception as e:
                try:
                    flask_app.logger.exception(f"[auto-settle] error: {e}")
                except Exception:
                    pass
            time.sleep(10)  # 10s 轮询
    Thread(target=_loop, daemon=True).start()


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

    @app.route('/finance', methods=['GET'])
    def finance_report():
        if not session.get('role'):
            return redirect('/login')

        role = session.get('role')            # 'admin' or 'agent'
        username = session.get('username')
        current_agent_id = session.get('agent_id')

        # 兜底：若 session 没 agent_id，就用用户名查一次（可选）
        if role != 'admin' and not current_agent_id and username:
            ag = Agent.query.filter_by(username=username).first()
            if ag:
                current_agent_id = ag.id

        # 读取日期（YYYY-MM-DD），默认今天
        today_str = datetime.now().strftime("%Y-%m-%d")
        start_date_str = request.args.get('start_date') or today_str
        end_date_str   = request.args.get('end_date')   or today_str

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date   = datetime.strptime(end_date_str,   "%Y-%m-%d").date()
        except ValueError:
            start_date = end_date = datetime.now().date()
            start_date_str = end_date_str = today_str

        # —— 口径选择：按“创建时间(created_at)”统计（推荐、最直观） ——
        bet_date_col = cast(Bet2D.created_at, Date)

        # 如需按“开奖期号(code=YYYYMMDD/HHMM)”的日期统计，可改为：
        # bet_date_col = func.to_date(func.substr(Bet2D.code, 1, 8), 'YYYYMMDD')

        # 1) 营业额（六个金额相加）
        sales_q = (
            db.session.query(
                Bet2D.agent_id.label('agent_id'),
                func.coalesce(func.sum(
                    func.coalesce(Bet2D.amount_n1, 0) +
                    func.coalesce(Bet2D.amount_n,  0) +
                    func.coalesce(Bet2D.amount_b,  0) +
                    func.coalesce(Bet2D.amount_s,  0) +
                    func.coalesce(Bet2D.amount_ds, 0) +
                    func.coalesce(Bet2D.amount_ss, 0)
                ), 0).label('sales')
            )
            .filter(
                Bet2D.status.in_(('active', 'locked')),
                bet_date_col >= start_date,
                bet_date_col <= end_date
            )
            .group_by(Bet2D.agent_id)
        )
        if role != 'admin' and current_agent_id:
            sales_q = sales_q.filter(Bet2D.agent_id == current_agent_id)

        sales_rows = sales_q.all()
        sales_by_agent = {row.agent_id: Decimal(row.sales or 0) for row in sales_rows}

        # 2) 中奖金额：用 WinningRecord2D.payout（不含本金）
        #    这里按“开奖期号 code 的日期”统计更合理：从 code 的 YYYYMMDD 取日期
        win_date_col = func.to_date(func.substr(WinningRecord2D.code, 1, 8), 'YYYYMMDD')
        wins_q = (
            db.session.query(
                WinningRecord2D.agent_id.label('agent_id'),
                func.coalesce(func.sum(WinningRecord2D.payout), 0).label('win_amount')
            )
            .filter(
                win_date_col >= start_date,
                win_date_col <= end_date
            )
            .group_by(WinningRecord2D.agent_id)
        )
        if role != 'admin' and current_agent_id:
            wins_q = wins_q.filter(WinningRecord2D.agent_id == current_agent_id)

        win_rows = wins_q.all()
        wins_by_agent = {row.agent_id: Decimal(row.win_amount or 0) for row in win_rows}

        # 3) 汇总&净额
        COMMISSION_RATE = Decimal('0.10')  # 佣金 10%
        all_agent_ids = sorted(set(sales_by_agent.keys()) | set(wins_by_agent.keys()))
        result_rows = []
        totals = {'sales': Decimal('0'), 'commission': Decimal('0'),
                  'win': Decimal('0'), 'net': Decimal('0')}

        for aid in all_agent_ids:
            sales = sales_by_agent.get(aid, Decimal('0'))
            win   = wins_by_agent.get(aid, Decimal('0'))
            commission = (sales * COMMISSION_RATE).quantize(Decimal('0.01'))
            net = (sales - commission - win).quantize(Decimal('0.01'))

            totals['sales']      += sales
            totals['commission'] += commission
            totals['win']        += win
            totals['net']        += net

            result_rows.append({
                'agent_id': aid,
                'sales': float(sales),
                'commission': float(commission),
                'win': float(win),
                'net': float(net),
            })

        for k in totals:
            totals[k] = float(Decimal(totals[k]).quantize(Decimal('0.01')))

        return render_template(
            'report_finance.html',
            start_date=start_date_str,
            end_date=end_date_str,
            rows=result_rows,
            totals=totals,
            commission_rate=float(COMMISSION_RATE)
        )

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
    @app.route('/2d/history')
    @login_required
    def history_2d():
        today = date.today().strftime("%Y-%m-%d")
        start_date_str = request.args.get('start_date', today)
        end_date_str   = request.args.get('end_date',   start_date_str)

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date   = datetime.strptime(end_date_str,   "%Y-%m-%d").date()
        except ValueError:
            start_date = end_date = date.today()
            start_date_str = end_date_str = today

        q = (db.session.query(Bet2D)
             .filter(Bet2D.status.in_(('active','locked')),
                     cast(Bet2D.created_at, Date) >= start_date,
                     cast(Bet2D.created_at, Date) <= end_date))

        # 非管理员只看自己的：
        if session.get('role') != 'admin':
            q = q.filter(Bet2D.agent_id == session.get('agent_id'))

        rows = q.order_by(Bet2D.order_code.asc(), Bet2D.id.asc()).all()

        # 序列化为 rows_js（你原先已有就复用）
        rows_js = [{
            "order_code": r.order_code,
            "agent_id": r.agent_id,
            "market": r.market,
            "code": r.code,
            "number": r.number,
            "amount_n1": float(r.amount_n1 or 0),
            "amount_n":  float(r.amount_n  or 0),
            "amount_b":  float(r.amount_b  or 0),
            "amount_s":  float(r.amount_s  or 0),
            "amount_ds": float(r.amount_ds or 0),
            "amount_ss": float(r.amount_ss or 0),
        } for r in rows]

        return render_template('history_2d.html',
                               start_date=start_date_str,
                               end_date=end_date_str,
                               rows_js=rows_js)

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

    # -------------- 结算：管理员手动触发 --------------
    @app.post("/admin/settle_now")
    @admin_required
    def admin_settle_now():
        n = settle_due_draws()
        return jsonify(ok=True, created=int(n))

    @app.post("/admin/settle_one")
    @admin_required
    def admin_settle_one():
        payload = request.get_json(silent=True) or {}
        code = (request.form.get("code") or payload.get("code") or "").strip()
        market = (request.form.get("market") or payload.get("market") or "").strip()
        if not code or not market:
            return jsonify(ok=False, error="missing code/market"), 400
        n = settle_one(code, market)
        return jsonify(ok=True, created=int(n))

    # 启动自动结算守护线程
    start_settle_daemon(app)

    return app


# 供 gunicorn 使用：app:app
app = create_app()
