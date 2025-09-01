import time
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from models import db, Bet2D, WinningRecord2D, DrawResult
from odds_config_2d import ODDS_2D
from app import create_app

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")

app = create_app()
scheduler = BackgroundScheduler(timezone=str(MY_TZ))


def code_for_slot(dt):
    # 期号固定为当小时 :50，例如 20250901/1950
    return dt.strftime("%Y%m%d") + f"/{dt.hour:02d}50"


def _to_int2(s: str) -> int:
    try:
        return int(s.strip())
    except:
        return -1


def job_lock_bets_2d():
    with app.app_context():
        now = datetime.now(MY_TZ)
        slot_code = code_for_slot(now)
        q = (Bet2D.query
             .filter(Bet2D.code == slot_code, Bet2D.status == 'active'))
        updated = q.update({
            Bet2D.status: 'locked',
            Bet2D.locked_at: now
        }, synchronize_session=False)
        db.session.commit()
        print(f"[2D] {now:%F %T} 锁注完成：code={slot_code}，rows={updated}")


def job_process_winning_2d():
    with app.app_context():
        now = datetime.now(MY_TZ)
        slot_code = code_for_slot(now)

        # 读当期开奖
        draw_map = {}
        for dr in DrawResult.query.filter_by(code=slot_code).all():
            specials_list = [x.strip() for x in (dr.specials or '').split(',') if x.strip()]
            draw_map[dr.market] = {"head": dr.head.strip(), "specials": specials_list}

        if not draw_map:
            print(f"[2D] {now:%F %T} 未找到当期开什么：code={slot_code}，跳过")
            return

        # 幂等：清理当期旧中奖记录
        WinningRecord2D.query.filter_by(code=slot_code).delete()
        db.session.commit()

        total_hits = 0
        bets = Bet2D.query.filter(Bet2D.code == slot_code, Bet2D.status == 'locked').all()

        for b in bets:
            if b.market not in draw_map:
                continue
            head = draw_map[b.market]["head"]
            specials = draw_map[b.market]["specials"]

            head_i = _to_int2(head)
            is_big = (0 <= head_i <= 99) and (head_i >= 50)
            is_odd = (0 <= head_i <= 99) and (head_i % 2 == 1)

            def emit(hit_type: str, stake: Decimal, odds_key: str):
                nonlocal total_hits
                if stake is None:
                    return
                stake = Decimal(stake)
                if stake <= 0:
                    return
                odds = ODDS_2D[odds_key]
                payout = stake * (odds - Decimal("1"))
                db.session.add(WinningRecord2D(
                    bet_id=b.id, agent_id=b.agent_id, market=b.market,
                    code=b.code, number=b.number,
                    hit_type=hit_type, stake=stake, odds=odds, payout=payout
                ))
                total_hits += 1

            # N1
            if Decimal(b.amount_n1 or 0) > 0 and b.number == head:
                emit("N1", b.amount_n1, "N1")

            # N（分头奖/特奖）
            if Decimal(b.amount_n or 0) > 0:
                if b.number == head:
                    emit("N_HEAD", b.amount_n, "N_HEAD")
                elif b.number in specials:
                    emit("N_SPECIAL", b.amount_n, "N_SPECIAL")

            # 属性类按头奖
            if Decimal(b.amount_b or 0) > 0 and is_big:
                emit("B", b.amount_b, "B")
            if Decimal(b.amount_s or 0) > 0 and not is_big and head_i >= 0:
                emit("S", b.amount_s, "S")
            if Decimal(b.amount_ds or 0) > 0 and is_odd:
                emit("DS", b.amount_ds, "DS")
            if Decimal(b.amount_ss or 0) > 0 and not is_odd and head_i >= 0:
                emit("SS", b.amount_ss, "SS")

        db.session.commit()
        print(f"[2D] {now:%F %T} 验奖完成：code={slot_code}，命中记录数={total_hits}")


# 调度（09:49–23:49 锁注；09:52–23:52 验奖）
scheduler.add_job(job_lock_bets_2d, CronTrigger(hour="9-23", minute=49, timezone=str(MY_TZ)), id="lock_bets_2d", replace_existing=True)
scheduler.add_job(job_process_winning_2d, CronTrigger(hour="9-23", minute=52, timezone=str(MY_TZ)), id="process_winning_2d", replace_existing=True)

scheduler.start()
print("[2D] Scheduler started.")

# worker 常驻
while True:
    time.sleep(3600)
