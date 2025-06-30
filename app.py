from flask import Flask, render_template, request, redirect, session, url_for
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
        number = request.form.get('number')
        bets = {
            '2D': request.form.get('bet_2d'),
            '单': request.form.get('bet_single'),
            '双': request.form.get('bet_double'),
            '大': request.form.get('bet_big'),
            '小': request.form.get('bet_small')
        }
        game_ids = request.form.getlist('game_ids')

        for game_id in game_ids:
            for type_name, amount in bets.items():
                if amount:
                    db.session.add(Bet(
                        game_id=int(game_id),
                        number=number if type_name == '2D' else None,
                        type=type_name,
                        amount=float(amount)
                    ))
        db.session.commit()
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
