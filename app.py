from flask import Flask, render_template, request, redirect, session, url_for, flash, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from datetime import date

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://db_4m_user:xiOe63X4iaczwTAcNfUYwS8oWrDExkHX@dpg-d11rb03uibrs73eh87vg-a/db_4m'
db = SQLAlchemy(app)

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
    created_at = db.Column(db.DateTime, server_default=db.func.now())

@app.route('/bet', methods=['GET', 'POST'])
def bet():
    if not session.get('logged_in'):
        return redirect('/')

    today = date.today()
    games = Game.query.filter_by(date=today).order_by(Game.hour).all()

    if request.method == 'POST':
        rows = int(request.form.get('rows'))
        for i in range(rows):
            number = request.form.get(f'number_{i}')
            if not number:
                continue  # 没有填号码就跳过

            # 获取每个类型金额
            amount_2d = request.form.get(f'2d_{i}') or 0
            amount_single = request.form.get(f'single_{i}') or 0
            amount_double = request.form.get(f'double_{i}') or 0
            amount_big = request.form.get(f'big_{i}') or 0
            amount_small = request.form.get(f'small_{i}') or 0

            # 所有转为数字
            a2d = float(amount_2d or 0)
            asg = float(amount_single or 0)
            adb = float(amount_double or 0)
            abg = float(amount_big or 0)
            asm = float(amount_small or 0)

            total = a2d + asg + adb + abg + asm

            # 获取所有勾选的时段
            time_slots = request.form.getlist(f'games_{i}')
            if not time_slots:
                continue  # 没有时段跳过（可选）

            time_slots_int = [int(t) for t in time_slots]

            # 保存到数据库
            bet = Bet(
                number=number,
                amount_2d=a2d,
                amount_single=asg,
                amount_double=adb,
                amount_big=abg,
                amount_small=asm,
                total=total,
                time_slots=time_slots_int
            )
            db.session.add(bet)
        db.session.commit()
        flash("✅ 下注成功！")
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
            return redirect('/bet')
        else:
            error = '账号或密码错误'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')
