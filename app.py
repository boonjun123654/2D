from flask import Flask, render_template, request, redirect, session, url_for, flash, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from datetime import date,datetime
import pytz

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://db_4m_user:xiOe63X4iaczwTAcNfUYwS8oWrDExkHX@dpg-d11rb03uibrs73eh87vg-a/db_4m'
db = SQLAlchemy(app)

class Agent(db.Model):
    __tablename__ = 'agent_2d'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

# 定义模型
class Game(db.Model):
    __tablename__ = 'games'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)
    hour = db.Column(db.Integer)
    status = db.Column(db.String)

class Bet(db.Model):
    __tablename__ = 'bets_2d'
    id = db.Column(db.Integer, primary_key=True)
    round = db.Column(db.String)  # 可留空
    number = db.Column(db.String(2), nullable=False)
    amount_2d = db.Column(db.Numeric, default=0)
    amount_single = db.Column(db.Numeric, default=0)
    amount_double = db.Column(db.Numeric, default=0)
    amount_big = db.Column(db.Numeric, default=0)
    amount_small = db.Column(db.Numeric, default=0)
    time_slots = db.Column(db.ARRAY(db.Integer), default=[])
    total = db.Column(db.Numeric, default=0)
    bet_date = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, nullable=False)

def get_malaysia_time():
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    return datetime.now(tz)

@app.route('/bet', methods=['GET', 'POST'])
def bet():
    if not session.get('logged_in'):
        return redirect('/')

    today = date.today()
    games = Game.query.filter_by(date=today).order_by(Game.hour).all()

    if request.method == 'POST':
        rows = int(request.form.get('rows'))
        summary_lines = []
        total_amount = 0

        for i in range(rows):
            number = request.form.get(f'number_{i}')
            if not number:
                continue

            # 获取每项金额
            a2d = float(request.form.get(f'2d_{i}') or 0)
            asg = float(request.form.get(f'single_{i}') or 0)
            adb = float(request.form.get(f'double_{i}') or 0)
            abg = float(request.form.get(f'big_{i}') or 0)
            asm = float(request.form.get(f'small_{i}') or 0)

            time_slots = request.form.getlist(f'games_{i}')
            if not time_slots:
                continue
            time_slots_int = [int(t) for t in time_slots]
            slot_count = len(time_slots_int)

            # 修正下注总额：金额 × 时段数量
            total = (a2d + asg + adb + abg + asm) * slot_count

            # 存入数据库
            now = get_malaysia_time()
            bet = Bet(
                number=number,
                amount_2d=a2d,
                amount_single=asg,
                amount_double=adb,
                amount_big=abg,
                amount_small=asm,
                total=total,
                time_slots=time_slots_int,
                bet_date=now.date(),
                created_at=now
            )
            db.session.add(bet)

            # 准备 flash 文本
            if a2d: summary_lines.append(f'{number}={int(a2d * slot_count)}')
            if asg: summary_lines.append(f'D={int(asg * slot_count)}')
            if adb: summary_lines.append(f'T={int(adb * slot_count)}')
            if abg: summary_lines.append(f'B={int(abg * slot_count)}')
            if asm: summary_lines.append(f'S={int(asm * slot_count)}')
            total_amount += total

        db.session.commit()
        summary_text = "\n".join(summary_lines)
        flash(f"✅ 下注成功\n\n{summary_text}\nTotal {int(total_amount)}")
        return redirect('/bet')

    return render_template('bet.html', games=games)

@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == 'admin' and password == '1234':  # ✅ 自定义用户名密码
            session['logged_in'] = True
            return redirect('/menu')
        else:
            error = '账号或密码错误'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')

@app.route('/bets', methods=['GET', 'POST'])
def view_bets():
    if not session.get('logged_in'):
        return redirect('/')

    bets = []
    selected_date = None
    total_all = 0

    if request.method == 'POST':
        date_str = request.form.get('date')  # 格式 yyyy-mm-dd
        if date_str:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            bets = Bet.query.filter_by(bet_date=selected_date).all()
            total_all = sum(float(b.total) for b in bets)

    return render_template('view_bets.html', bets=bets, selected_date=selected_date, total_all=total_all)

@app.route('/menu')
def menu():
    return render_template('menu.html')

@app.template_filter('trim_zeros')
def trim_zeros(value):
    if isinstance(value, str):
        if '.' in value:
            value = value.rstrip('0').rstrip('.')
        return value
    elif isinstance(value, float):
        s = '%.2f' % value
        return s.rstrip('0').rstrip('.') if '.' in s else s
    return value

@app.route('/admin/agents', methods=['GET', 'POST'])
def manage_agents():
    if not session.get('logged_in'):
        return redirect('/')

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('请输入完整的账号密码')
        else:
            existing = Agent.query.filter_by(username=username).first()
            if existing:
                flash('❌ 用户名已存在')
            else:
                agent = Agent(
                    username=username,
                    password=password  # 可加密：generate_password_hash(password)
                )
                db.session.add(agent)
                db.session.commit()
                flash(f'✅ 成功创建代理：{username}')

    agents = Agent.query.all()
    return render_template('manage_agents.html', agents=agents)

