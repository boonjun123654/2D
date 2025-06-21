# game_state.py
from collections import defaultdict
from datetime import datetime

class GameState:
    def __init__(self):
        self.round_id = None
        self.bets = defaultdict(int)  # number -> total amount
        self.is_betting_open = False
        self.winning_w = None  # 单个头奖号码
        self.winning_t = []    # 特别奖号码列表

    def start_new_round(self):
        now = datetime.now()
        self.round_id = f"{now.strftime('%y%m%d')}{str(now.microsecond)[:3]}"
        self.bets.clear()
        self.is_betting_open = True
        self.winning_w = None  # 单个头奖号码
        self.winning_t = []    # 特别奖号码列表

    def add_bet(self, number: int, amount: int):
        if self.is_betting_open:
            self.bets[number] += amount

    def lock_bets(self):
        self.is_betting_open = False

    def get_total_bets(self):
        return dict(self.bets)

    def get_top_bettor(self):
        if not self.bets:
            return None, 0
        sorted_bets = sorted(self.bets.items(), key=lambda x: x[1], reverse=True)
        return sorted_bets[0]
