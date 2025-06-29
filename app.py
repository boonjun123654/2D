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
    game_id = db.Column(db.Integer)
    number = db.Column(db.String)
    type = db.Column(db.String)
    amount = db.Column(db.Numeric)

@app.route('/bet', methods=['GET', 'POST'])
def bet():
    if not session.get('logged_in'):
        return redirect('/')

    today = date.today()
    games = Game.query.filter_by(date=today).order_by(Game.hour).all()

    if request.method == 'POST':
        rows = int(request.form.get('rows'))
        for i in range(rows):
            number = request.form.get(f'number_{i}') or None
            for bet_type in ['2d', 'single', 'double', 'big', 'small']:
                amount = request.form.get(f'{bet_type}_{i}')
                if amount:
                    for game_id in request.form.getlist(f'games_{i}'):
                        bet = Bet(
                            game_id=int(game_id),
                            number=number if bet_type == '2d' else None,
                            type='2D' if bet_type == '2d' else bet_type,
                            amount=float(amount)
                        )
                        db.session.add(bet)
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
